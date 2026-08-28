"""Controlled project skills and extension manifests.

Skills are deliberately data-first.  A Markdown skill can enrich a prompt and
be inspected offline; executable Python/command entries are discovered and
validated but remain disabled unless an application explicitly supplies an
approved executor.  This prevents a file dropped into a repository from
silently acquiring shell, network or write access.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
import fnmatch
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .security.redaction import redact_text
from .security.workspace import WorkspaceGuard, WorkspaceViolation, assert_no_path_alias


MAX_SKILLS = 128
MAX_SKILL_BYTES = 256_000
MAX_DESCRIPTION_CHARS = 4_000
MAX_CONTENT_CHARS = 40_000
_SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ALLOWED_FIELDS = {
    "id", "name", "version", "description", "entry", "entry_type", "side_effect", "approval",
    "timeout_seconds", "max_output_chars", "allowed_paths", "input_schema", "enabled",
}
_SCHEMA_FIELDS = {"type", "properties", "required", "additionalProperties", "description", "items", "enum"}
_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
_SIDE_EFFECTS = {"read_only", "write", "command", "network"}
_APPROVALS = {"none", "interactive", "always"}


class SkillError(ValueError):
    """A skill manifest or content cannot be trusted."""


@dataclass(frozen=True)
class SkillManifest:
    id: str
    name: str
    version: str
    description: str
    entry: str | None = None
    entry_type: str = "markdown"
    side_effect: str = "read_only"
    approval: str = "none"
    timeout_seconds: float = 10.0
    max_output_chars: int = 20_000
    allowed_paths: tuple[str, ...] = ()
    input_schema: dict[str, Any] = None  # type: ignore[assignment]
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID.fullmatch(self.id):
            raise SkillError("skill id must be lowercase bounded text")
        for field, value, limit in (("name", self.name, 128), ("description", self.description, MAX_DESCRIPTION_CHARS)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise SkillError(f"skill {field} is invalid")
        if not isinstance(self.version, str) or not _SEMVER.fullmatch(self.version):
            raise SkillError("skill version must be semantic version text")
        if self.entry is not None and (not isinstance(self.entry, str) or not self.entry or len(self.entry) > 1_000 or "\\" in self.entry or Path(self.entry).is_absolute() or ".." in Path(self.entry).parts):
            raise SkillError("skill entry must be a relative bounded path")
        if self.entry_type not in {"markdown", "python", "command"}:
            raise SkillError("skill entry_type is unsupported")
        if self.entry_type != "markdown" and not self.entry:
            raise SkillError("executable skills require an entry")
        if self.side_effect not in _SIDE_EFFECTS:
            raise SkillError("skill side_effect is unsupported")
        if self.approval not in _APPROVALS:
            raise SkillError("skill approval is unsupported")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0 or self.timeout_seconds > 120:
            raise SkillError("skill timeout_seconds must be between 0 and 120")
        if isinstance(self.max_output_chars, bool) or not isinstance(self.max_output_chars, int) or not 1 <= self.max_output_chars <= MAX_CONTENT_CHARS:
            raise SkillError("skill max_output_chars is invalid")
        if not isinstance(self.allowed_paths, tuple) or any(not isinstance(item, str) or not item or len(item) > 512 or "\\" in item or Path(item).is_absolute() or ".." in Path(item).parts for item in self.allowed_paths):
            raise SkillError("skill allowed_paths must be relative paths")
        schema = {} if self.input_schema is None else self.input_schema
        if not isinstance(schema, dict) or schema.get("type", "object") != "object":
            raise SkillError("skill input_schema must be an object schema")
        _validate_schema(schema)
        if self.entry_type == "markdown" and self.side_effect != "read_only":
            raise SkillError("Markdown skills must declare side_effect=read_only")
        if self.entry_type == "markdown" and self.entry is not None:
            raise SkillError("Markdown skills cannot declare an executable entry")
        if not isinstance(self.enabled, bool):
            raise SkillError("skill enabled must be boolean")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["allowed_paths"] = list(self.allowed_paths)
        result["input_schema"] = self.input_schema or {}
        return result


@dataclass(frozen=True)
class Skill:
    manifest: SkillManifest
    path: str
    content: str = ""
    diagnostics: tuple[str, ...] = ()

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        result = {"path": self.path, "manifest": self.manifest.to_dict(), "diagnostics": list(self.diagnostics)}
        if include_content:
            result["content"] = self.content[:MAX_CONTENT_CHARS]
        return result


@dataclass(frozen=True)
class SkillInvocation:
    skill_id: str
    version: str
    ok: bool
    output: str
    error: str | None = None
    permission: str = "read_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillExecutor:
    """Execute a manifest entry only after explicit application approval.

    The entry is a workspace-relative regular file, never an arbitrary shell
    string.  Python entries receive a bounded JSON document on stdin; command
    entries are launched directly without ``shell=True``.  Environment
    variables resembling credentials are removed.
    """

    def __init__(self, guard: WorkspaceGuard, *, max_output_chars: int = MAX_CONTENT_CHARS):
        self.guard = guard
        if isinstance(max_output_chars, bool) or not 1 <= max_output_chars <= MAX_CONTENT_CHARS:
            raise ValueError("max_output_chars is invalid")
        self.max_output_chars = max_output_chars

    def __call__(self, skill: Skill, arguments: dict[str, Any]) -> str:
        return self.execute(skill, arguments, approved=False)

    def execute(self, skill: Skill, arguments: dict[str, Any], *, approved: bool = False) -> str:
        manifest = skill.manifest
        if not manifest.entry:
            raise SkillError("skill has no executable entry")
        # Executable entries are code, even when their declared effect is
        # read-only.  They must never run merely because a manifest claims
        # that they do not write; an explicit approval is still required.
        if not approved:
            raise SkillError("executable skill requires explicit approval")
        entry = self.guard.resolve(manifest.entry, must_exist=True)
        entry_relative = self.guard.relative(entry)
        if manifest.allowed_paths and not any(fnmatch.fnmatchcase(entry_relative, pattern) for pattern in manifest.allowed_paths):
            raise SkillError("skill entry is outside its allowed_paths")
        if not entry.is_file():
            raise SkillError("skill entry is not a regular file")
        try:
            from .security.workspace import assert_no_path_alias
            assert_no_path_alias(entry)
        except WorkspaceViolation as exc:
            raise SkillError("skill entry is a symlink or junction alias") from exc
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded) > MAX_CONTENT_CHARS:
            raise SkillError("skill arguments exceed the safety limit")
        env = {name: value for name, value in os.environ.items() if not any(marker in name.upper() for marker in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE"))}
        command = [sys.executable, str(entry)] if manifest.entry_type == "python" else [str(entry)]
        started = time.monotonic()
        try:
            completed = subprocess.run(command, cwd=self.guard.root, input=encoded, text=True, capture_output=True, timeout=manifest.timeout_seconds, env=env, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SkillError("skill execution timed out") from exc
        except OSError as exc:
            raise SkillError(f"skill could not start: {type(exc).__name__}") from exc
        output = (completed.stdout or "") + (("\n[stderr]\n" + completed.stderr) if completed.stderr else "")
        output = redact_text(output)[: self.max_output_chars]
        if completed.returncode != 0:
            raise SkillError(f"skill exited with code {completed.returncode}: {output[:500]}")
        return output


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith(("\"", "'")) and value[-1:] == value[0]:
        return value[1:-1]
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise SkillError("invalid front matter JSON value") from exc
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _validate_schema(schema: Any, *, depth: int = 0) -> None:
    """Validate the deliberately small JSON-Schema subset used by skills."""
    if depth > 12 or not isinstance(schema, dict) or len(schema) > 32:
        raise SkillError("skill input_schema is too deep or too large")
    unknown = set(schema) - _SCHEMA_FIELDS
    if unknown:
        raise SkillError("skill input_schema contains unknown fields: " + ", ".join(sorted(str(item) for item in unknown)))
    schema_type = schema.get("type", "object")
    if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
        raise SkillError("skill input_schema type is invalid")
    description = schema.get("description")
    if description is not None and (not isinstance(description, str) or len(description) > 1_000):
        raise SkillError("skill input_schema description is invalid")
    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or len(enum) > 64:
            raise SkillError("skill input_schema enum is invalid")
        try:
            json.dumps(enum, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise SkillError("skill input_schema enum must be finite JSON") from exc
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise SkillError("skill input_schema additionalProperties must be boolean")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict) or len(properties) > 128:
            raise SkillError("skill input_schema properties are invalid")
        for name, child in properties.items():
            if not isinstance(name, str) or not name or len(name) > 128:
                raise SkillError("skill input_schema property name is invalid")
            _validate_schema(child, depth=depth + 1)
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or len(required) > 128 or any(not isinstance(item, str) or not item or len(item) > 128 for item in required) or len(set(required)) != len(required):
            raise SkillError("skill input_schema required is invalid")
        if isinstance(properties, dict) and any(item not in properties for item in required):
            raise SkillError("skill input_schema required property is not declared")
    if "items" in schema:
        _validate_schema(schema["items"], depth=depth + 1)


def _matches_schema(value: Any, schema: dict[str, Any], *, depth: int = 0) -> bool:
    """Apply the same bounded schema subset to invocation arguments."""
    if depth > 12:
        return False
    schema_type = schema.get("type", "object")
    if schema_type == "null":
        valid_type = value is None
    elif schema_type == "boolean":
        valid_type = isinstance(value, bool)
    elif schema_type == "string":
        valid_type = isinstance(value, str)
    elif schema_type == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif schema_type == "number":
        valid_type = isinstance(value, (int, float)) and not isinstance(value, bool) and (not isinstance(value, float) or math.isfinite(value))
    elif schema_type == "array":
        valid_type = isinstance(value, list)
    else:
        valid_type = isinstance(value, dict)
    if not valid_type:
        return False
    enum = schema.get("enum")
    if enum is not None and not any(value == candidate and type(value) is type(candidate) for candidate in enum):
        return False
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(name not in value for name in required):
            return False
        if schema.get("additionalProperties", True) is False and any(name not in properties for name in value):
            return False
        return all(_matches_schema(item, properties[name], depth=depth + 1) for name, item in value.items() if name in properties)
    if schema_type == "array" and "items" in schema:
        return all(_matches_schema(item, schema["items"], depth=depth + 1) for item in value)
    return True


def _front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise SkillError("skill front matter is not closed")
    raw = "\n".join(lines[1:end])
    if raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SkillError("skill front matter JSON is invalid") from exc
        if not isinstance(data, dict):
            raise SkillError("skill front matter must be an object")
    else:
        data = {}
        for line in raw.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                raise SkillError("skill front matter line must contain ':'")
            key, value = line.split(":", 1)
            data[key.strip()] = _parse_scalar(value)
    return data, "\n".join(lines[end + 1:])


def _manifest_from(raw: dict[str, Any], *, default_id: str, content: str) -> SkillManifest:
    unknown = set(raw) - _ALLOWED_FIELDS
    if unknown:
        raise SkillError("skill manifest contains unknown fields: " + ", ".join(sorted(unknown)))
    values = dict(raw)
    values.setdefault("id", default_id)
    values.setdefault("name", values["id"])
    values.setdefault("version", "0.1.0")
    values.setdefault("description", content.strip().splitlines()[0][:256] if content.strip() else values["name"])
    values.setdefault("entry_type", "markdown")
    values.setdefault("side_effect", "read_only")
    values.setdefault("approval", "none")
    values.setdefault("timeout_seconds", 10.0)
    values.setdefault("max_output_chars", 20_000)
    values.setdefault("allowed_paths", ())
    values.setdefault("input_schema", {})
    if isinstance(values.get("allowed_paths"), list):
        values["allowed_paths"] = tuple(values["allowed_paths"])
    return SkillManifest(**values)


class SkillLoader:
    """Discover only explicitly named, workspace-local skill locations."""

    def __init__(self, guard: WorkspaceGuard, *, directories: Iterable[str | Path] | None = None, max_skills: int = MAX_SKILLS):
        if isinstance(max_skills, bool) or not 1 <= max_skills <= MAX_SKILLS:
            raise ValueError("max_skills must be between 1 and 128")
        self.guard = guard
        self.directories = tuple(directories or ("skills", ".forgecode/skills"))
        self.max_skills = max_skills
        self.errors: list[str] = []

    def discover(self) -> tuple[Skill, ...]:
        self.errors = []
        files: list[Path] = []
        for directory in self.directories:
            try:
                root = self.guard.resolve(directory)
                assert_no_path_alias(root)
            except (OSError, ValueError) as exc:
                self.errors.append(f"{directory}: {type(exc).__name__}")
                continue
            if not root.exists():
                continue
            if not root.is_dir():
                self.errors.append(f"{directory}: not a directory")
                continue
            try:
                files.extend(path for path in sorted(root.rglob("*.md"), key=lambda item: self.guard.relative(item)) if path.is_file())
                files.extend(path for path in sorted(root.rglob("*.json"), key=lambda item: self.guard.relative(item)) if path.is_file() and path.name.endswith(".skill.json"))
            except OSError as exc:
                self.errors.append(f"{directory}: {type(exc).__name__}")
        skills: list[Skill] = []
        seen: set[str] = set()
        for path in files[: self.max_skills * 2]:
            if len(skills) >= self.max_skills:
                self.errors.append("skill count exceeds safety limit")
                break
            try:
                relative = self.guard.relative(path)
                assert_no_path_alias(path)
                if path.stat().st_size > MAX_SKILL_BYTES:
                    raise SkillError("skill file exceeds size limit")
                raw_bytes = path.read_bytes()
                text = raw_bytes.decode("utf-8")
                if path.name.endswith(".skill.json"):
                    raw = json.loads(text)
                    if not isinstance(raw, dict):
                        raise SkillError("skill manifest must be an object")
                    raw = dict(raw)
                    content_value = raw.pop("content", "")
                    if not isinstance(content_value, str):
                        raise SkillError("skill content must be text")
                    content = content_value
                else:
                    raw, content = _front_matter(text)
                default_id = re.sub(r"[^a-z0-9_.-]+", "-", path.stem.lower()).strip("-") or "skill"
                manifest = _manifest_from(raw, default_id=default_id[:64], content=content)
                if manifest.id in seen:
                    raise SkillError(f"duplicate skill id: {manifest.id}")
                seen.add(manifest.id)
                skills.append(Skill(manifest, relative, redact_text(content)[:MAX_CONTENT_CHARS]))
            except (OSError, UnicodeError, json.JSONDecodeError, SkillError, ValueError) as exc:
                self.errors.append(f"{self.guard.relative(path) if path.exists() else path.name}: {type(exc).__name__}: {str(exc)[:200]}")
        return tuple(skills)


class SkillRegistry:
    def __init__(self, skills: Iterable[Skill] = ()):
        self._skills = {skill.manifest.id: skill for skill in skills}

    def list(self) -> tuple[Skill, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))

    def get(self, skill_id: str) -> Skill:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise SkillError(f"unknown skill: {skill_id}") from exc

    def invoke(self, skill_id: str, arguments: dict[str, Any] | None = None, *, executor: Callable[[Skill, dict[str, Any]], str] | None = None, approved: bool = False) -> SkillInvocation:
        skill = self.get(skill_id)
        manifest = skill.manifest
        if not manifest.enabled:
            return SkillInvocation(skill_id, manifest.version, False, "skill is disabled", "disabled", manifest.side_effect)
        args = {} if arguments is None else arguments
        if not isinstance(args, dict):
            return SkillInvocation(skill_id, manifest.version, False, "skill arguments must be an object", "invalid_arguments", manifest.side_effect)
        schema = manifest.input_schema or {}
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            return SkillInvocation(skill_id, manifest.version, False, "skill input schema is invalid", "invalid_schema", manifest.side_effect)
        missing = [item for item in required if item not in args]
        if missing:
            return SkillInvocation(skill_id, manifest.version, False, "missing required arguments", "invalid_arguments", manifest.side_effect)
        if not _matches_schema(args, schema):
            return SkillInvocation(skill_id, manifest.version, False, "invalid skill arguments", "invalid_arguments", manifest.side_effect)
        if manifest.entry_type in {"python", "command"} and not approved:
            return SkillInvocation(skill_id, manifest.version, False, "skill invocation requires explicit approval", "approval_required", manifest.side_effect)
        try:
            if manifest.entry_type == "markdown":
                output = skill.content
            elif executor is not None:
                if isinstance(executor, SkillExecutor):
                    output = executor.execute(skill, args, approved=approved)
                else:
                    output = executor(skill, args)
            else:
                return SkillInvocation(skill_id, manifest.version, False, "executable skills require an approved executor", "executor_unavailable", manifest.side_effect)
            if not isinstance(output, str):
                return SkillInvocation(skill_id, manifest.version, False, "skill executor returned non-text output", "invalid_result", manifest.side_effect)
            return SkillInvocation(skill_id, manifest.version, True, redact_text(output)[: manifest.max_output_chars], None, manifest.side_effect)
        except Exception as exc:
            return SkillInvocation(skill_id, manifest.version, False, f"skill failed: {type(exc).__name__}", type(exc).__name__, manifest.side_effect)


__all__ = ["Skill", "SkillError", "SkillExecutor", "SkillInvocation", "SkillLoader", "SkillManifest", "SkillRegistry"]
