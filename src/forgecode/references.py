"""Explicit, bounded context references and read-only Git virtual sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import itertools
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable

from .security.redaction import redact_text
from .security.workspace import WorkspaceGuard, WorkspaceViolation
from .context_policy import is_ignored_context_path, is_sensitive_context_path

MAX_REFERENCE_COUNT = 128
MAX_REFERENCE_FILE_BYTES = 256_000
MAX_REFERENCE_TOTAL_CHARS = 120_000
MAX_DIRECTORY_MATCHES = 256
MAX_GIT_OUTPUT_CHARS = 40_000


@dataclass(frozen=True)
class ReferenceDiagnostic:
    code: str
    reference: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ReferenceItem:
    kind: str
    reference: str
    path: str | None = None
    digest: str | None = None
    size: int = 0
    language: str | None = None
    content: str = ""
    priority: int = 0
    truncated: bool = False
    omitted_reason: str | None = None

    def to_dict(self, *, include_content: bool = False) -> dict:
        result = asdict(self)
        if not include_content:
            result.pop("content", None)
        return result


@dataclass(frozen=True)
class ReferenceResolution:
    items: tuple[ReferenceItem, ...] = ()
    diagnostics: tuple[ReferenceDiagnostic, ...] = ()
    total_chars: int = 0

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256("|".join(f"{item.kind}:{item.path or item.reference}:{item.digest or ''}" for item in self.items).encode("utf-8")).hexdigest()

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)

    def render(self, max_chars: int = MAX_REFERENCE_TOTAL_CHARS) -> str:
        chunks: list[str] = []
        used = 0
        for item in self.items:
            text = item.content if item.content else f"[{item.reference}]"
            chunk = f"[reference {item.kind} {item.path or item.reference}]\n{text}"
            if used + len(chunk) > max_chars:
                break
            chunks.append(chunk)
            used += len(chunk)
        return "\n\n".join(chunks)


_REF_RE = re.compile(r"(?<![\w])@(?:\"([^\"]+)\"|'([^']+)'|([^\s,;]+))")
_LANGUAGE = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript/React", ".go": "Go", ".rs": "Rust", ".java": "Java", ".json": "JSON", ".md": "Markdown", ".toml": "TOML"}


def parse_references(prompt: str) -> tuple[str, ...]:
    """Return references in appearance order; ``\\@`` is ordinary text."""
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")
    found: list[str] = []
    escaped = prompt.replace("\\@", "__FORGECODE_ESCAPED_AT__")
    for match in _REF_RE.finditer(escaped):
        value = next(group for group in match.groups() if group is not None)
        if value == "@" or value.startswith("@"):
            continue
        found.append(value)
    return tuple(dict.fromkeys(found))


def _sensitive(relative: str) -> bool:
    return is_sensitive_context_path(relative)


def _ignored_candidate(guard: WorkspaceGuard, path: Path) -> bool:
    return is_ignored_context_path(guard, path)


def _safe_relative(guard: WorkspaceGuard, path: Path) -> str:
    return guard.relative(path)


class ReferenceResolver:
    def __init__(self, guard: WorkspaceGuard, *, max_references: int = MAX_REFERENCE_COUNT, max_matches: int = MAX_DIRECTORY_MATCHES, max_file_bytes: int = MAX_REFERENCE_FILE_BYTES, max_total_chars: int = MAX_REFERENCE_TOTAL_CHARS, git_timeout: float = 5.0):
        self.guard = guard
        self.max_references = max_references
        self.max_matches = max_matches
        self.max_file_bytes = max_file_bytes
        self.max_total_chars = max_total_chars
        self.git_timeout = git_timeout
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (max_references, max_matches, max_file_bytes, max_total_chars)):
            raise ValueError("reference limits must be positive integers")

    def resolve_prompt(self, prompt: str) -> ReferenceResolution:
        return self.resolve(parse_references(prompt))

    def resolve(self, references: Iterable[str]) -> ReferenceResolution:
        items: list[ReferenceItem] = []
        diagnostics: list[ReferenceDiagnostic] = []
        seen: set[tuple[str, str]] = set()
        total = 0
        refs = list(itertools.islice(references, self.max_references + 1))
        if len(refs) > self.max_references:
            diagnostics.append(ReferenceDiagnostic("reference_limit", "", f"only the first {self.max_references} references are considered"))
            refs = refs[: self.max_references]
        for reference in refs:
            if not isinstance(reference, str) or not reference or len(reference) > 2_000 or "\x00" in reference:
                diagnostics.append(ReferenceDiagnostic("reference_invalid", "", "reference must be non-empty text of at most 2000 characters", "error"))
                continue
            if reference.startswith("git:"):
                item = self._git(reference[4:])
                if item is None:
                    diagnostics.append(ReferenceDiagnostic("git_error", reference, "Git context unavailable", "error"))
                    continue
                key = (item.kind, item.reference)
                if key not in seen:
                    available = self.max_total_chars - total
                    if available <= 0:
                        diagnostics.append(ReferenceDiagnostic("context_limit", reference, "Git reference omitted after total context limit"))
                    else:
                        content = item.content[:available]
                        item = ReferenceItem(**{**item.__dict__, "content": content, "truncated": item.truncated or len(content) < len(item.content), "omitted_reason": "context_limit" if len(content) < len(item.content) else item.omitted_reason})
                        items.append(item); seen.add(key); total += len(item.content)
                continue
            paths: list[Path]
            has_glob = any(char in reference for char in "*?[")
            if has_glob:
                raw_pattern = reference.replace("\\", "/")
                if not raw_pattern or Path(reference).is_absolute() or any(part == ".." for part in raw_pattern.split("/")):
                    diagnostics.append(ReferenceDiagnostic("path_rejected", reference, "glob must stay inside the workspace", "error"))
                    continue
                pattern = raw_pattern[2:] if raw_pattern.startswith("./") else raw_pattern
                try:
                    paths = self._scan_files(self.guard.root, pattern=pattern)
                except OSError as exc:
                    diagnostics.append(ReferenceDiagnostic("scan_error", reference, f"glob could not be scanned: {type(exc).__name__}", "error"))
                    paths = []
            else:
                try:
                    lexical = Path(reference)
                    if not lexical.is_absolute():
                        lexical = self.guard.root / lexical
                    path = self.guard.resolve(reference, must_exist=True)
                    if path != lexical.absolute():
                        diagnostics.append(ReferenceDiagnostic("unsafe_path", reference, "symlink or junction references are not context", "error"))
                        continue
                except (OSError, ValueError, WorkspaceViolation) as exc:
                    diagnostics.append(ReferenceDiagnostic("path_rejected", reference, f"reference path rejected: {type(exc).__name__}", "error"))
                    continue
                try:
                    relative = _safe_relative(self.guard, path)
                except (OSError, ValueError):
                    diagnostics.append(ReferenceDiagnostic("path_rejected", reference, "reference path is outside workspace")); continue
                if _sensitive(relative):
                    diagnostics.append(ReferenceDiagnostic("sensitive_omitted", reference, "sensitive or private path is not context")); continue
                if _ignored_candidate(self.guard, path):
                    diagnostics.append(ReferenceDiagnostic("ignored_omitted", reference, "ignored path is not context")); continue
                if path.is_dir():
                    try:
                        paths = self._scan_files(path)
                    except OSError as exc:
                        diagnostics.append(ReferenceDiagnostic("scan_error", reference, f"directory could not be scanned: {type(exc).__name__}", "error"))
                        paths = []
                else:
                    paths = [path]
            paths = sorted(paths, key=lambda candidate: candidate.as_posix().lower())
            if len(paths) > self.max_matches:
                diagnostics.append(ReferenceDiagnostic("match_limit", reference, f"directory/glob matches capped at {self.max_matches}"))
                paths = paths[: self.max_matches]
            for candidate in paths:
                item, diagnostic = self._file(reference, candidate, total)
                if diagnostic:
                    diagnostics.append(diagnostic)
                    continue
                assert item is not None
                key = (item.kind, item.path or reference)
                if key in seen:
                    diagnostics.append(ReferenceDiagnostic("duplicate", reference, "duplicate reference omitted")); continue
                if total + len(item.content) > self.max_total_chars:
                    diagnostics.append(ReferenceDiagnostic("context_limit", reference, "reference omitted after total context limit")); continue
                items.append(item); seen.add(key); total += len(item.content)
        return ReferenceResolution(tuple(items), tuple(diagnostics), total)

    def _scan_files(self, root: Path, *, pattern: str | None = None) -> list[Path]:
        paths: list[Path] = []
        for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
            directory_path = Path(directory)
            safe_names: list[str] = []
            for name in sorted(names, key=str.lower):
                candidate = directory_path / name
                try:
                    resolved = self.guard.resolve(candidate)
                    if resolved == candidate.absolute() and not _ignored_candidate(self.guard, candidate):
                        safe_names.append(name)
                except (OSError, ValueError, WorkspaceViolation):
                    continue
            names[:] = safe_names
            for name in sorted(filenames, key=str.lower):
                candidate = directory_path / name
                try:
                    resolved = self.guard.resolve(candidate, must_exist=True)
                    if resolved != candidate.absolute() or not resolved.is_file() or _ignored_candidate(self.guard, resolved):
                        continue
                    relative = self.guard.relative(resolved)
                    if pattern is not None and not Path(relative).match(pattern):
                        continue
                    paths.append(resolved)
                    if len(paths) > self.max_matches:
                        return paths
                except (OSError, ValueError, WorkspaceViolation):
                    continue
        return paths

    def _file(self, reference: str, path: Path, total: int) -> tuple[ReferenceItem | None, ReferenceDiagnostic | None]:
        try:
            resolved = self.guard.resolve(path, must_exist=True)
            if resolved != path.absolute() or not resolved.is_file():
                return None, ReferenceDiagnostic("unsafe_path", reference, "symlink or non-file reference rejected", "error")
            relative = self.guard.relative(resolved)
            if _sensitive(relative):
                return None, ReferenceDiagnostic("sensitive_omitted", reference, "sensitive or private path is not context")
            if _ignored_candidate(self.guard, resolved):
                return None, ReferenceDiagnostic("ignored_omitted", reference, "ignored path is not context")
            before_stat = resolved.stat()
            if before_stat.st_size > self.max_file_bytes:
                return None, ReferenceDiagnostic("file_limit", reference, "file exceeds reference byte limit")
            raw = resolved.read_bytes()
            after_stat = resolved.stat()
            if (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0)) != (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0)):
                return None, ReferenceDiagnostic("read_changed", reference, "file changed while it was read", "error")
            if b"\x00" in raw:
                return None, ReferenceDiagnostic("binary_omitted", reference, "binary file is not context")
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, ReferenceDiagnostic("encoding_error", reference, "file is not valid UTF-8", "error")
        except (OSError, ValueError, WorkspaceViolation) as exc:
            return None, ReferenceDiagnostic("read_error", reference, f"file could not be read: {type(exc).__name__}", "error")
        available = max(0, self.max_total_chars - total)
        truncated = len(content) > available
        content = redact_text(content[:available])
        return ReferenceItem("file", reference, relative, hashlib.sha256(raw).hexdigest(), len(raw), _LANGUAGE.get(resolved.suffix.lower()), content, 100, truncated, "context_limit" if truncated else None), None

    def _git(self, name: str) -> ReferenceItem | None:
        safe_pathspecs = ["--", ".", ":(exclude).env", ":(exclude).env.*", ":(exclude).forgecode/**", ":(exclude)docs/goals/**", ":(exclude)*.pem", ":(exclude)*.key", ":(exclude)*.secret"]
        allowed = {"status": ["git", "status", "--short", *safe_pathspecs], "diff": ["git", "diff", "--no-ext-diff", *safe_pathspecs], "log": ["git", "log", "-n", "20", "--oneline", "--decorate"]}
        if name not in allowed:
            return None
        try:
            completed = subprocess.run(allowed[name], cwd=self.guard.root, capture_output=True, text=True, timeout=self.git_timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        output = redact_text((completed.stdout or "") + (("\n[stderr]\n" + completed.stderr) if completed.stderr else ""))
        truncated = len(output) > MAX_GIT_OUTPUT_CHARS
        output = output[:MAX_GIT_OUTPUT_CHARS]
        if completed.returncode != 0:
            return None
        return ReferenceItem("git", f"git:{name}", None, hashlib.sha256(output.encode("utf-8")).hexdigest(), len(output.encode("utf-8")), "Git", output, 90, truncated, "output_limit" if truncated else None)


def resolve_references(guard: WorkspaceGuard, prompt_or_references: str | Iterable[str]) -> ReferenceResolution:
    resolver = ReferenceResolver(guard)
    if isinstance(prompt_or_references, str):
        return resolver.resolve_prompt(prompt_or_references)
    return resolver.resolve(prompt_or_references)


__all__ = ["ReferenceDiagnostic", "ReferenceItem", "ReferenceResolution", "ReferenceResolver", "parse_references", "resolve_references"]
