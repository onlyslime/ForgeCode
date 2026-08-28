"""Typed project test profiles and bounded verification evidence.

The command verifier used by the agent accepts a shell string for backwards
compatibility.  Project test profiles deliberately use *argv arrays* instead:
there is no shell interpolation, and the profile loader rejects ambiguous
string commands before they can reach an executor.  This module is provider
and CLI neutral; :class:`TestProfileRunner` can be called by both the
``forgecode test`` command and the agent's ``/test`` path.

The canonical configuration file is ``.forgecode/tests.toml``::

    version = 1
    default_profile = "quick"

    [profiles.quick]
    command = ["python", "-m", "pytest", "-q"]
    cwd = "."
    timeout_seconds = 120
    env_allow = ["CI"]
    [profiles.quick.output]
    stdout_chars = 20000
    stderr_chars = 10000
    total_chars = 30000
    [profiles.quick.expected_exit]
    codes = [0]

``.forgecode/test-profiles.toml`` is accepted as a compatibility filename.
The loader is strict: unknown keys, shell-string commands, unsafe cwd values,
secret environment names, non-finite numbers and oversized values fail closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import tomllib
from typing import Any, Callable, Iterable, Mapping, Sequence
import uuid

from .security.redaction import redact_text
from .security.workspace import WorkspaceGuard, WorkspaceViolation, assert_no_path_alias


class TestProfileError(ValueError):
    """A profile or test invocation is invalid or unsafe."""


TEST_PROFILE_SCHEMA_VERSION = 1
MAX_TEST_PROFILE_BYTES = 1_000_000
MAX_PROFILES = 64
MAX_PROFILE_NAME = 64
MAX_DESCRIPTION_CHARS = 512
MAX_COMMAND_ARGS = 64
MAX_ARG_CHARS = 4_096
MAX_CWD_CHARS = 512
MAX_ENV_NAMES = 128
MAX_TIMEOUT_SECONDS = 86_400.0
MAX_OUTPUT_CHARS = 2_000_000
MAX_FINGERPRINT_FILES = 10_000
MAX_FINGERPRINT_FILE_BYTES = 4 * 1024 * 1024
PROCESS_CLEANUP_GRACE_SECONDS = 1.0

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_BASE_ENV = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "VIRTUAL_ENV",
    }
)
_SECRET_ENV_MARKERS = (
    "API_KEY",
    "APIKEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "COOKIE",
    "AUTHORIZATION",
    "PRIVATE_KEY",
    "CREDENTIAL",
)
_SHELL_LAUNCHERS = {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}


def _reject_nonfinite(value: str) -> None:
    raise TestProfileError(f"non-finite TOML number is not allowed: {value}")


def _bounded_text(value: Any, field_name: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > limit or "\x00" in value:
        qualifier = "string" if allow_empty else "non-empty string"
        raise TestProfileError(f"{field_name} must be a {qualifier} of at most {limit} characters")
    return value


def _fit_preview(value: Any, limit: int) -> str:
    """Redact and bound one evidence preview, including its marker."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    marker = "\n[output truncated]"
    if len(marker) >= limit:
        return marker[:limit]
    return text[: limit - len(marker)] + marker


def _bounded_int(value: Any, field_name: str, lower: int, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise TestProfileError(f"{field_name} must be an integer between {lower} and {upper}")
    return value


def _bounded_number(value: Any, field_name: str, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not lower <= float(value) <= upper:
        raise TestProfileError(f"{field_name} must be a finite number between {lower:g} and {upper:g}")
    return float(value)


def _validate_argv(value: Any, field_name: str, *, required: bool = True) -> tuple[str, ...] | None:
    if value is None and not required:
        return None
    if isinstance(value, str):
        raise TestProfileError(f"{field_name} must be an argv array; shell command strings are forbidden")
    if not isinstance(value, (list, tuple)) or not value:
        raise TestProfileError(f"{field_name} must be a non-empty array of strings")
    if len(value) > MAX_COMMAND_ARGS:
        raise TestProfileError(f"{field_name} contains too many arguments (maximum {MAX_COMMAND_ARGS})")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _bounded_text(item, f"{field_name}[{index}]", MAX_ARG_CHARS)
        result.append(text)
    # A shell launcher is not inherently unsafe when invoked with argv, but
    # allowing ``cmd /c`` or ``powershell -Command`` recreates shell-string
    # ambiguity.  Profiles should express direct executable invocations.
    launcher = Path(result[0]).name.lower()
    if launcher in _SHELL_LAUNCHERS:
        lowered = " ".join(item.lower() for item in result[1:])
        if any(flag in lowered for flag in ("/c", "-c", "-command", "--command", "-encodedcommand")):
            raise TestProfileError(f"{field_name} may not invoke a shell command interpreter")
    return tuple(result)


def _validate_env_names(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TestProfileError(f"{field_name} must be an array of environment variable names")
    if len(value) > MAX_ENV_NAMES:
        raise TestProfileError(f"{field_name} contains too many names (maximum {MAX_ENV_NAMES})")
    names: list[str] = []
    for index, item in enumerate(value):
        name = _bounded_text(item, f"{field_name}[{index}]", 128)
        if not _ENV_RE.fullmatch(name):
            raise TestProfileError(f"{field_name}[{index}] is not a valid environment variable name")
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_ENV_MARKERS):
            raise TestProfileError(f"{field_name}[{index}] is a credential-bearing environment variable")
        if name not in names:
            names.append(name)
    return tuple(names)


@dataclass(frozen=True)
class OutputQuota:
    """Independent output limits for stdout, stderr, and their total."""

    stdout_chars: int = 20_000
    stderr_chars: int = 20_000
    total_chars: int = 40_000

    def validate(self, prefix: str = "output") -> None:
        for name, value in (
            ("stdout_chars", self.stdout_chars),
            ("stderr_chars", self.stderr_chars),
            ("total_chars", self.total_chars),
        ):
            _bounded_int(value, f"{prefix}.{name}", 128, MAX_OUTPUT_CHARS)
        if self.total_chars < min(self.stdout_chars, self.stderr_chars):
            raise TestProfileError(f"{prefix}.total_chars cannot be smaller than an individual stream quota")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ExpectedExit:
    """The only successful process outcomes are explicitly listed codes."""

    codes: tuple[int, ...] = (0,)

    def validate(self, prefix: str = "expected_exit") -> None:
        if not isinstance(self.codes, tuple) or not self.codes or len(self.codes) > 32:
            raise TestProfileError(f"{prefix}.codes must contain 1-32 exit codes")
        for code in self.codes:
            _bounded_int(code, f"{prefix}.codes[]", -(2**31), 2**31 - 1)

    def to_dict(self) -> dict[str, list[int]]:
        return {"codes": list(self.codes)}


@dataclass(frozen=True)
class TestProfile:
    name: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: float = 120.0
    env_allow: tuple[str, ...] = ()
    output: OutputQuota = field(default_factory=OutputQuota)
    expected_exit: ExpectedExit = field(default_factory=ExpectedExit)
    setup: tuple[str, ...] | None = None
    teardown: tuple[str, ...] | None = None
    description: str = ""
    approval: str = "required"

    def validate(self, guard: WorkspaceGuard | None = None) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name) or len(self.name) > MAX_PROFILE_NAME:
            raise TestProfileError("profile name must contain only letters, digits, '.', '-' or '_'")
        _validate_argv(self.command, "command")
        _bounded_text(self.cwd, "cwd", MAX_CWD_CHARS)
        cwd_path = Path(self.cwd)
        if cwd_path.is_absolute() or any(part == ".." for part in cwd_path.parts):
            raise TestProfileError("cwd must be a relative path inside the workspace")
        if self.cwd.replace("\\", "/").startswith("//"):
            raise TestProfileError("cwd must be a relative path inside the workspace")
        if guard is not None:
            try:
                resolved = guard.resolve(cwd_path)
            except (WorkspaceViolation, OSError) as exc:
                raise TestProfileError("cwd is outside or aliases the workspace") from exc
            if not resolved.is_dir():
                raise TestProfileError("cwd must refer to an existing workspace directory")
        _bounded_number(self.timeout_seconds, "timeout_seconds", 0.1, MAX_TIMEOUT_SECONDS)
        _validate_env_names(self.env_allow, "env_allow")
        self.output.validate()
        self.expected_exit.validate()
        if self.setup is not None:
            _validate_argv(self.setup, "setup")
        if self.teardown is not None:
            _validate_argv(self.teardown, "teardown")
        _bounded_text(self.description, "description", MAX_DESCRIPTION_CHARS, allow_empty=True)
        if self.approval not in {"required", "auto", "deny"}:
            raise TestProfileError("approval must be required, auto, or deny")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["command"] = list(self.command)
        value["env_allow"] = list(self.env_allow)
        value["setup"] = list(self.setup) if self.setup is not None else None
        value["teardown"] = list(self.teardown) if self.teardown is not None else None
        value["output"] = self.output.to_dict()
        value["expected_exit"] = self.expected_exit.to_dict()
        return value


@dataclass(frozen=True)
class TestProfiles:
    profiles: tuple[TestProfile, ...]
    default_profile: str = "default"
    source: str | None = None
    schema_version: int = TEST_PROFILE_SCHEMA_VERSION

    def validate(self, guard: WorkspaceGuard | None = None) -> None:
        _bounded_int(self.schema_version, "version", 1, TEST_PROFILE_SCHEMA_VERSION)
        if not self.profiles or len(self.profiles) > MAX_PROFILES:
            raise TestProfileError(f"profiles must contain 1-{MAX_PROFILES} named profiles")
        names: set[str] = set()
        for profile in self.profiles:
            profile.validate(guard)
            if profile.name in names:
                raise TestProfileError(f"duplicate profile: {profile.name}")
            names.add(profile.name)
        if self.default_profile not in names:
            raise TestProfileError(f"default profile not found: {self.default_profile}")

    def get(self, name: str | None = None, *, env: Mapping[str, str] | None = None) -> TestProfile:
        selected = name
        if selected is None and env is not None:
            selected = env.get("FORGECODE_TEST_PROFILE")
        selected = selected or self.default_profile
        if not isinstance(selected, str) or not selected.strip():
            raise TestProfileError("profile selection must be a non-empty name")
        for profile in self.profiles:
            if profile.name == selected:
                return profile
        raise TestProfileError(f"profile not found: {selected}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "default_profile": self.default_profile,
            "source": self.source,
            "profiles": [profile.to_dict() for profile in self.profiles],
        }


class TestProfileLoader:
    """Load a strict ``.forgecode/tests.toml`` profile document."""

    DEFAULT_FILENAMES = ("tests.toml", "test-profiles.toml")

    def __init__(self, workspace: Path, path: Path | None = None):
        self.guard = WorkspaceGuard(Path(workspace))
        if path is None:
            self.path = self._discover_path()
        else:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.guard.root / candidate
            try:
                self.path = self.guard.resolve(candidate)
            except WorkspaceViolation as exc:
                raise TestProfileError("test profile path is outside workspace") from exc

    def _discover_path(self) -> Path | None:
        directory = self.guard.root / ".forgecode"
        for filename in self.DEFAULT_FILENAMES:
            candidate = directory / filename
            try:
                if os.path.lexists(candidate):
                    assert_no_path_alias(candidate, message="test profile file is a symlink or junction alias")
                    resolved = self.guard.resolve(candidate, must_exist=True)
                    if not resolved.is_file():
                        raise TestProfileError("test profile path is not a regular file")
                    return resolved
            except WorkspaceViolation as exc:
                raise TestProfileError(str(exc)) from exc
        return None

    def load(self, *, path: Path | None = None) -> TestProfiles:
        source = self.path if path is None else Path(path)
        if source is None:
            result = TestProfiles((TestProfile("default", ("python", "-m", "pytest", "-q")),), "default", None)
            result.validate(self.guard)
            return result
        if not source.is_absolute():
            source = self.guard.root / source
        try:
            source = self.guard.resolve(source, must_exist=True)
            assert_no_path_alias(source, message="test profile file is a symlink or junction alias")
        except (WorkspaceViolation, FileNotFoundError) as exc:
            raise TestProfileError("test profile file is outside workspace or missing") from exc
        try:
            before_stat = source.stat()
            if before_stat.st_size > MAX_TEST_PROFILE_BYTES:
                raise TestProfileError(f"test profile TOML exceeds the {MAX_TEST_PROFILE_BYTES}-byte safety limit")
            with source.open("rb") as stream:
                raw = tomllib.load(stream)
            after_stat = source.stat()
            if after_stat.st_size > MAX_TEST_PROFILE_BYTES:
                raise TestProfileError(f"test profile TOML exceeds the {MAX_TEST_PROFILE_BYTES}-byte safety limit")
            assert_no_path_alias(source, message="test profile file changed to a symlink or junction while it was read")
            before_identity = (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0))
            after_identity = (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0))
            if before_identity != after_identity:
                raise TestProfileError("test profile file changed while it was read")
        except TestProfileError:
            raise
        except WorkspaceViolation as exc:
            raise TestProfileError(str(exc)) from exc
        except (OSError, tomllib.TOMLDecodeError, RecursionError) as exc:
            if isinstance(exc, RecursionError):
                raise TestProfileError("test profile TOML nesting exceeds the safety limit") from exc
            raise TestProfileError(f"invalid test profile TOML: {type(exc).__name__}") from exc
        if not isinstance(raw, dict):
            raise TestProfileError("test profile root must be a table")
        allowed_root = {"version", "default_profile", "profiles"}
        unknown = set(raw) - allowed_root
        if unknown:
            raise TestProfileError("unknown test profile fields: " + ", ".join(sorted(unknown)))
        version = raw.get("version", TEST_PROFILE_SCHEMA_VERSION)
        _bounded_int(version, "version", TEST_PROFILE_SCHEMA_VERSION, TEST_PROFILE_SCHEMA_VERSION)
        default = raw.get("default_profile", "default")
        _bounded_text(default, "default_profile", MAX_PROFILE_NAME)
        profiles_raw = raw.get("profiles")
        if not isinstance(profiles_raw, dict):
            raise TestProfileError("profiles must be a table")
        if len(profiles_raw) > MAX_PROFILES:
            raise TestProfileError(f"profiles contains too many entries (maximum {MAX_PROFILES})")
        profiles: list[TestProfile] = []
        profile_allowed = {"command", "cwd", "timeout_seconds", "env_allow", "output", "expected_exit", "setup", "teardown", "description", "approval"}
        for name, profile_raw in profiles_raw.items():
            _bounded_text(name, "profile name", MAX_PROFILE_NAME)
            if not _NAME_RE.fullmatch(name):
                raise TestProfileError(f"invalid profile name: {name}")
            if not isinstance(profile_raw, dict):
                raise TestProfileError(f"profile {name} must be a table")
            unknown_profile = set(profile_raw) - profile_allowed
            if unknown_profile:
                raise TestProfileError(f"unknown fields in profile {name}: " + ", ".join(sorted(unknown_profile)))
            command = _validate_argv(profile_raw.get("command"), f"profiles.{name}.command")
            assert command is not None
            cwd = profile_raw.get("cwd", ".")
            if not isinstance(cwd, str):
                raise TestProfileError(f"profiles.{name}.cwd must be a relative string")
            timeout = _bounded_number(profile_raw.get("timeout_seconds", 120), f"profiles.{name}.timeout_seconds", 0.1, MAX_TIMEOUT_SECONDS)
            env_allow = _validate_env_names(profile_raw.get("env_allow", ()), f"profiles.{name}.env_allow")
            output_raw = profile_raw.get("output", {})
            if not isinstance(output_raw, dict):
                raise TestProfileError(f"profiles.{name}.output must be a table")
            unknown_output = set(output_raw) - {"stdout_chars", "stderr_chars", "total_chars"}
            if unknown_output:
                raise TestProfileError(f"unknown output fields in profile {name}: " + ", ".join(sorted(unknown_output)))
            output = OutputQuota(output_raw.get("stdout_chars", 20_000), output_raw.get("stderr_chars", 20_000), output_raw.get("total_chars", 40_000))
            output.validate(f"profiles.{name}.output")
            expected_raw = profile_raw.get("expected_exit", {})
            if not isinstance(expected_raw, dict):
                raise TestProfileError(f"profiles.{name}.expected_exit must be a table")
            unknown_expected = set(expected_raw) - {"codes"}
            if unknown_expected:
                raise TestProfileError(f"unknown expected_exit fields in profile {name}: " + ", ".join(sorted(unknown_expected)))
            codes_raw = expected_raw.get("codes", [0])
            if not isinstance(codes_raw, (list, tuple)):
                raise TestProfileError(f"profiles.{name}.expected_exit.codes must be an array")
            codes = tuple(_bounded_int(code, f"profiles.{name}.expected_exit.codes[]", -(2**31), 2**31 - 1) for code in codes_raw)
            expected = ExpectedExit(codes)
            expected.validate(f"profiles.{name}.expected_exit")
            setup = _validate_argv(profile_raw.get("setup"), f"profiles.{name}.setup", required=False)
            teardown = _validate_argv(profile_raw.get("teardown"), f"profiles.{name}.teardown", required=False)
            description = _bounded_text(profile_raw.get("description", ""), f"profiles.{name}.description", MAX_DESCRIPTION_CHARS, allow_empty=True)
            approval = profile_raw.get("approval", "required")
            if approval not in {"required", "auto", "deny"}:
                raise TestProfileError(f"profiles.{name}.approval must be required, auto, or deny")
            profile = TestProfile(name, command, str(cwd), timeout, env_allow, output, expected, setup, teardown, description, approval)
            profile.validate(self.guard)
            profiles.append(profile)
        # Expose only a workspace-relative source label; machine-readable
        # callers must never receive a developer's private absolute path.
        source_label = source.relative_to(self.guard.root).as_posix()
        result = TestProfiles(tuple(profiles), default, source_label, int(version))
        result.validate(self.guard)
        return result


@dataclass(frozen=True)
class TestEvidence:
    """Bounded, serialisable evidence for one profile invocation."""

    schema_version: int
    evidence_id: str
    profile: str
    command: tuple[str, ...]
    cwd: str
    approval: str
    started_at: str
    ended_at: str
    duration_seconds: float
    timeout_seconds: float
    timed_out: bool
    cancelled: bool
    truncated: bool
    exit_code: int | None
    stdout_digest: str
    stderr_digest: str
    stdout_preview: str
    stderr_preview: str
    before_fingerprint: str
    after_fingerprint: str
    verification_status: str
    ok: bool
    error_code: str | None = None
    steps: tuple[dict[str, Any], ...] = ()

    def validate(self) -> None:
        if self.schema_version != TEST_PROFILE_SCHEMA_VERSION:
            raise TestProfileError("unsupported test evidence schema")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", self.evidence_id):
            raise TestProfileError("evidence_id is invalid")
        _bounded_text(self.profile, "profile", MAX_PROFILE_NAME)
        _validate_argv(self.command, "command")
        _bounded_text(self.cwd, "cwd", MAX_CWD_CHARS)
        if self.approval not in {"approved", "denied", "mode_denied", "cancelled"}:
            raise TestProfileError("approval evidence value is invalid")
        for field_name, value in (("timed_out", self.timed_out), ("cancelled", self.cancelled), ("truncated", self.truncated), ("ok", self.ok)):
            if not isinstance(value, bool):
                raise TestProfileError(f"{field_name} must be a boolean")
        if self.exit_code is not None:
            _bounded_int(self.exit_code, "exit_code", -(2**31), 2**31 - 1)
        for field_name, value in (("started_at", self.started_at), ("ended_at", self.ended_at)):
            _bounded_text(value, field_name, 128)
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise TestProfileError(f"{field_name} must be an ISO-8601 timestamp") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise TestProfileError(f"{field_name} must include a timezone")
        for name, value in (("stdout_digest", self.stdout_digest), ("stderr_digest", self.stderr_digest), ("before_fingerprint", self.before_fingerprint), ("after_fingerprint", self.after_fingerprint)):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise TestProfileError(f"{name} must be a SHA-256 digest")
        for name, value in (("stdout_preview", self.stdout_preview), ("stderr_preview", self.stderr_preview)):
            _bounded_text(value, name, MAX_OUTPUT_CHARS, allow_empty=True)
        if self.verification_status not in {"passed", "failed", "denied", "timed_out", "cancelled", "skipped", "error"}:
            raise TestProfileError("verification_status is invalid")
        if self.ok and (self.verification_status != "passed" or self.timed_out or self.cancelled or self.exit_code is None):
            raise TestProfileError("only an untimed expected exit may be reported as passed")
        if self.timed_out and self.verification_status == "passed":
            raise TestProfileError("timed out checks cannot pass")
        _bounded_number(self.duration_seconds, "duration_seconds", 0.0, MAX_TIMEOUT_SECONDS + 60)
        _bounded_number(self.timeout_seconds, "timeout_seconds", 0.1, MAX_TIMEOUT_SECONDS)
        if self.error_code is not None:
            _bounded_text(self.error_code, "error_code", 128)
        if not isinstance(self.steps, tuple) or len(self.steps) > 16 or any(not isinstance(item, dict) for item in self.steps):
            raise TestProfileError("steps must be a bounded tuple of objects")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["command"] = list(self.command)
        value["steps"] = [dict(item) for item in self.steps]
        return value


@dataclass
class _Capture:
    quota: int
    digest: Any = field(default_factory=hashlib.sha256)
    preview: str = ""
    total_chars: int = 0
    truncated: bool = False
    quota_exceeded: bool = False
    done: threading.Event = field(default_factory=threading.Event)

    def feed(self, chunk: bytes) -> None:
        self.digest.update(chunk)
        text = chunk.decode("utf-8", errors="replace")
        self.total_chars += len(text)
        room = max(0, self.quota - len(self.preview))
        if room:
            self.preview += text[:room]
        if self.total_chars > self.quota:
            self.truncated = True
            self.quota_exceeded = True

    def value(self) -> str:
        text = self.preview
        if self.truncated:
            text += "\n[output truncated]"
        return text


def _terminate_process_tree(process: subprocess.Popen[Any]) -> str:
    if process.poll() is not None:
        return "already_exited"
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5, check=False)
            return "requested"
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            import signal

            os.killpg(process.pid, signal.SIGKILL)
            return "requested"
        except OSError:
            pass
    try:
        process.kill()
        return "requested"
    except OSError:
        return "unresolved"


def _cancel_requested(cancel: Callable[[], bool] | None) -> tuple[bool, str | None]:
    """Evaluate an untrusted cancellation callback fail-closed."""
    if cancel is None:
        return False, None
    try:
        return bool(cancel()), None
    except Exception as exc:
        return True, type(exc).__name__


def workspace_fingerprint(guard: WorkspaceGuard, *, max_files: int = MAX_FINGERPRINT_FILES) -> str:
    """Hash bounded file metadata/content without exposing file contents."""
    if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= MAX_FINGERPRINT_FILES:
        raise TestProfileError("max_files is outside the fingerprint budget")
    records: list[str] = []
    excluded = {".git", ".forgecode", ".venv", "__pycache__", "node_modules"}
    try:
        candidates = sorted(guard.root.rglob("*"), key=lambda path: path.as_posix())
    except OSError:
        candidates = []
    for path in candidates:
        if len(records) >= max_files:
            records.append(f"[file-limit:{max_files}]")
            break
        try:
            relative = path.relative_to(guard.root)
            if any(part in excluded for part in relative.parts):
                continue
            resolved = guard.resolve(path)
            stat_result = resolved.stat()
            if not resolved.is_file():
                continue
            digest = hashlib.sha256()
            remaining = MAX_FINGERPRINT_FILE_BYTES
            with resolved.open("rb") as stream:
                while remaining > 0:
                    chunk = stream.read(min(65_536, remaining))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
            records.append(f"{relative.as_posix()}\0{stat_result.st_size}\0{stat_result.st_mtime_ns}\0{digest.hexdigest()}")
        except (OSError, ValueError, WorkspaceViolation):
            records.append(f"[unreadable:{path.name}]")
    return hashlib.sha256("\n".join(records).encode("utf-8", errors="replace")).hexdigest()


class TestProfileRunner:
    """Execute a profile with approval, cancellation, deadlines and evidence."""

    def __init__(self, guard: WorkspaceGuard, *, approval: Any = None, session: Any = None, mode: str = "act", secrets: Iterable[str] = ()):
        self.guard = guard
        self.approval = approval
        self.session = session
        self.mode = str(mode)
        self.secrets = tuple(secret for secret in secrets if isinstance(secret, str) and secret)

    def run(
        self,
        profile: TestProfile,
        *,
        cancel: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
        approval: Any = None,
        session: Any = None,
        mode: str | None = None,
    ) -> TestEvidence:
        profile.validate(self.guard)
        selected_mode = self.mode if mode is None else str(mode)
        evidence_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        before = workspace_fingerprint(self.guard)
        effective_timeout = profile.timeout_seconds if timeout_seconds is None else _bounded_number(timeout_seconds, "timeout_seconds", 0.1, MAX_TIMEOUT_SECONDS)
        approval_policy = self.approval if approval is None else approval
        if selected_mode == "plan":
            evidence = self._make_evidence(evidence_id, profile, "mode_denied", started_at, started, before, None, False, False, False, "skipped", effective_timeout, (), "mode_denied")
            return self._persist(evidence, session=session, mode=selected_mode)
        cancel_now, cancel_error = _cancel_requested(cancel)
        if cancel_now:
            evidence = self._make_evidence(evidence_id, profile, "cancelled", started_at, started, before, None, False, True, False, "cancelled", effective_timeout, (), "cancelled")
            return self._persist(evidence, session=session, mode=selected_mode)
        try:
            if profile.approval == "deny":
                approved = False
            elif approval_policy is None:
                # ``auto`` is an explicit profile opt-in for non-interactive
                # callers, but a supplied global policy remains authoritative.
                approved = profile.approval == "auto"
            elif callable(approval_policy):
                approved = bool(approval_policy("test", {"profile": profile.name, "command": list(profile.command), "cwd": profile.cwd}))
            else:
                approve_method = getattr(approval_policy, "approve", None)
                approved = bool(approve_method and approve_method("test", {"profile": profile.name, "command": list(profile.command), "cwd": profile.cwd}))
        except Exception as exc:
            evidence = self._make_evidence(evidence_id, profile, "denied", started_at, started, before, None, False, False, False, "error", effective_timeout, (), "approval_error")
            return self._persist(evidence, session=session, mode=selected_mode)
        if not approved:
            evidence = self._make_evidence(evidence_id, profile, "denied", started_at, started, before, None, False, False, False, "denied", effective_timeout, (), "approval_denied")
            return self._persist(evidence, session=session, mode=selected_mode)
        cwd = self.guard.resolve(profile.cwd, must_exist=True)
        steps: list[dict[str, Any]] = []
        remaining_deadline = started + effective_timeout
        main_outcome: dict[str, Any] | None = None
        teardown_outcome: dict[str, Any] | None = None
        cancellation_after_main = False
        for phase, command in (("setup", profile.setup), ("main", profile.command), ("teardown", profile.teardown)):
            if command is None:
                continue
            cancel_now, _ = _cancel_requested(cancel)
            # If the main process could not be confirmed dead, running a
            # teardown command concurrently would create a second side
            # effect while the first one may still be active.  Leave the
            # profile in an explicit unresolved/error state for recovery
            # instead of pretending cleanup was safe.
            main_unresolved = bool(main_outcome and main_outcome.get("termination_result") == "unresolved")
            if phase == "teardown" and (main_outcome is None or cancel_now or main_unresolved):
                cancellation_after_main = cancel_now
                continue
            phase_budget = remaining_deadline - time.monotonic()
            if phase_budget <= 0:
                outcome = {
                    "exit_code": None,
                    "timed_out": True,
                    "cancelled": False,
                    "stdout": "",
                    "stderr": "",
                    "stdout_digest": hashlib.sha256(b"").hexdigest(),
                    "stderr_digest": hashlib.sha256(b"").hexdigest(),
                    "truncated": False,
                    "error_code": "timeout",
                }
            else:
                outcome = self._execute_argv(command, cwd, profile.output, phase_budget, cancel, profile.env_allow)
            steps.append({"phase": phase, **outcome})
            if phase == "main":
                main_outcome = outcome
                # A cancellation may arrive after the child exits but before
                # teardown starts. Preserve that terminal state; do not let an
                # expected main exit become a false pass.
                cancellation_after_main = _cancel_requested(cancel)[0]
            elif phase == "teardown":
                teardown_outcome = outcome
            if phase == "setup" and (outcome.get("timed_out") or outcome.get("cancelled") or outcome.get("exit_code") not in (0,)):
                break
        if main_outcome is None:
            main_outcome = {"exit_code": None, "timed_out": False, "cancelled": False, "stdout": "", "stderr": "", "stdout_digest": hashlib.sha256(b"").hexdigest(), "stderr_digest": hashlib.sha256(b"").hexdigest(), "truncated": False, "error_code": "setup_failed"}
        # The evidence describes the complete profile lifecycle, not only the
        # main command.  A setup/teardown timeout or cancellation must remain
        # visible at the top level; otherwise a caller could mistake a failed
        # lifecycle step for an ordinary main-command failure (and lose the
        # strongest reason that the verification was unsafe).
        timed_out = any(bool(step.get("timed_out")) for step in steps) or bool(main_outcome.get("timed_out"))
        cancelled = any(bool(step.get("cancelled")) for step in steps) or bool(main_outcome.get("cancelled")) or cancellation_after_main
        termination_unresolved = any(step.get("termination_result") == "unresolved" for step in steps)
        exit_code = main_outcome.get("exit_code")
        if termination_unresolved:
            status, error_code = "error", "termination_unresolved"
        elif timed_out:
            timeout_phase = next((str(step.get("phase")) for step in steps if step.get("timed_out")), "main")
            status, error_code = "timed_out", {
                "setup": "setup_timeout",
                "teardown": "teardown_timeout",
            }.get(timeout_phase, "timeout")
        elif cancelled:
            status, error_code = "cancelled", "cancelled"
        elif main_outcome.get("error_code") == "execution_error":
            status, error_code = "error", "execution_error"
        elif any(step.get("phase") == "setup" and step.get("exit_code") not in (0, None) for step in steps):
            status, error_code = "failed", "setup_failed"
        elif teardown_outcome is not None and (teardown_outcome.get("timed_out") or teardown_outcome.get("cancelled") or teardown_outcome.get("exit_code") not in (0, None)):
            if teardown_outcome.get("timed_out"):
                status, error_code = "timed_out", "teardown_timeout"
            elif teardown_outcome.get("cancelled"):
                status, error_code = "cancelled", "cancelled"
            else:
                status, error_code = "failed", "teardown_failed"
        elif exit_code in profile.expected_exit.codes:
            status, error_code = "passed", None
        else:
            status, error_code = "failed", "unexpected_exit"
        after = workspace_fingerprint(self.guard)
        evidence = self._make_evidence(evidence_id, profile, "approved", started_at, started, before, exit_code, timed_out, cancelled, any(bool(step.get("truncated")) for step in steps), status, effective_timeout, tuple(steps), error_code, stdout=main_outcome.get("stdout", ""), stderr=main_outcome.get("stderr", ""), stdout_digest=main_outcome.get("stdout_digest"), stderr_digest=main_outcome.get("stderr_digest"), after=after)
        return self._persist(evidence, session=session, mode=selected_mode)

    def _execute_argv(self, command: Sequence[str], cwd: Path, quota: OutputQuota, timeout: float, cancel: Callable[[], bool] | None, env_allow: Sequence[str] = ()) -> dict[str, Any]:
        env: dict[str, str] = {}
        for name in _SAFE_BASE_ENV:
            if name in os.environ:
                env[name] = os.environ[name]
        for name in env_allow:
            # ``TestProfile.validate`` already rejects credential-shaped names;
            # repeat the check here because this helper is intentionally public
            # enough to be called by tests/integrations directly.
            upper = str(name).upper()
            if _ENV_RE.fullmatch(str(name)) and not any(marker in upper for marker in _SECRET_ENV_MARKERS) and str(name) in os.environ:
                env[str(name)] = os.environ[str(name)]
        process_options: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        captures = {"stdout": _Capture(quota.stdout_chars), "stderr": _Capture(quota.stderr_chars)}
        try:
            process = subprocess.Popen(tuple(command), cwd=cwd, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **process_options)
        except OSError as exc:
            empty = hashlib.sha256(b"").hexdigest()
            return {"exit_code": None, "timed_out": False, "cancelled": False, "stdout": "", "stderr": "", "stdout_digest": empty, "stderr_digest": empty, "truncated": False, "error_code": "execution_error", "error": type(exc).__name__}
        threads: list[threading.Thread] = []
        for stream_name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            assert stream is not None
            capture = captures[stream_name]

            def consume(handle=stream, target=capture):
                try:
                    while True:
                        chunk = handle.read(8192)
                        if not chunk:
                            break
                        target.feed(chunk)
                finally:
                    target.done.set()

            thread = threading.Thread(target=consume, name=f"forgecode-test-{stream_name}", daemon=True)
            thread.start()
            threads.append(thread)
        deadline = time.monotonic() + max(0.1, timeout)
        timed_out = cancelled = False
        cancellation_error: str | None = None
        termination_result = ""
        while process.poll() is None:
            cancel_now, cancel_error = _cancel_requested(cancel)
            if cancel_now:
                cancelled = True
                cancellation_error = cancel_error
                # A failing cancellation predicate is itself a safety signal;
                # retain a bounded diagnostic rather than allowing the
                # callback exception to escape and make the invocation look
                # unrecorded.
                termination_result = _terminate_process_tree(process)
                if cancel_error and termination_result == "already_exited":
                    # Preserve the fact that the predicate itself failed in
                    # the phase error field below; process termination still
                    # follows the normal bounded path.
                    termination_result = "requested"
                break
            if time.monotonic() >= deadline:
                timed_out = True
                termination_result = _terminate_process_tree(process)
                break
            time.sleep(0.01)
        try:
            process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            termination_result = _terminate_process_tree(process)
            try:
                process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                termination_result = "unresolved"
        for thread in threads:
            thread.join(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
        # A child/descendant can retain an inherited stdout/stderr pipe even
        # after the direct process has exited.  Leaving the daemon reader
        # alive means the command's side effects and output stream are not
        # fully accounted for; fail closed instead of reporting a pass.
        if any(thread.is_alive() for thread in threads):
            termination_result = "unresolved"
        stdout = captures["stdout"].value()
        stderr = captures["stderr"].value()
        total_truncated = False
        total_room = max(0, quota.total_chars)
        if len(stdout) + len(stderr) > total_room:
            total_truncated = True
            # Preserve the complete digest but bound the evidence previews.
            marker = "\n[output truncated]"
            marker_len = len(marker)
            # Keep stdout first (it is generally the useful test report), then
            # fit stderr into the remaining budget.  Reserve room for one
            # marker whenever possible so the truncation is explicit without
            # exceeding ``total_chars``.
            if total_room <= marker_len:
                stdout, stderr = marker[:total_room], ""
            else:
                content_room = total_room - marker_len
                stdout_content = stdout[:content_room]
                stderr_content = stderr[: max(0, content_room - len(stdout_content))]
                combined = stdout_content + stderr_content
                if len(combined) < content_room:
                    # If the first stream was short, use the remaining room
                    # for the second stream (the expression above already
                    # does this; this branch documents the invariant).
                    pass
                stdout = stdout_content
                stderr = stderr_content
                if len(stdout) < content_room:
                    stderr += marker
                else:
                    stdout += marker
        return {
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_digest": captures["stdout"].digest.hexdigest(),
            "stderr_digest": captures["stderr"].digest.hexdigest(),
            "truncated": captures["stdout"].truncated or captures["stderr"].truncated or total_truncated,
            "termination_result": termination_result or ("already_exited" if process.returncode is not None else "unresolved"),
            "cancellation_error": cancellation_error,
        }

    def _make_evidence(self, evidence_id: str, profile: TestProfile, approval: str, started_at: str, started: float, before: str, exit_code: int | None, timed_out: bool, cancelled: bool, truncated: bool, status: str, timeout: float, steps: tuple[dict[str, Any], ...], error_code: str | None, *, stdout: str = "", stderr: str = "", stdout_digest: str | None = None, stderr_digest: str | None = None, after: str | None = None) -> TestEvidence:
        ended = datetime.now(timezone.utc).isoformat()
        safe_steps: list[dict[str, Any]] = []
        for item in steps[:16]:
            if not isinstance(item, dict):
                continue
            safe: dict[str, Any] = {}
            for key, value in item.items():
                if key in {"stdout", "stderr", "error"} and isinstance(value, str):
                    safe[key] = redact_text(value[:MAX_OUTPUT_CHARS], self.secrets)
                elif key == "command" and isinstance(value, (list, tuple)):
                    safe[key] = [redact_text(str(argument)[:MAX_ARG_CHARS], self.secrets) for argument in value[:MAX_COMMAND_ARGS]]
                else:
                    safe[key] = value
            safe_steps.append(safe)
        safe_stdout = _fit_preview(redact_text(stdout, self.secrets), profile.output.stdout_chars)
        safe_stderr = _fit_preview(redact_text(stderr, self.secrets), profile.output.stderr_chars)
        evidence = TestEvidence(TEST_PROFILE_SCHEMA_VERSION, evidence_id, profile.name, profile.command, profile.cwd, approval, started_at, ended, round(max(0.0, time.monotonic() - started), 3), timeout, timed_out, cancelled, truncated, exit_code, stdout_digest or hashlib.sha256(b"").hexdigest(), stderr_digest or hashlib.sha256(b"").hexdigest(), safe_stdout, safe_stderr, before, after or before, status, status == "passed", error_code, tuple(safe_steps))
        evidence.validate()
        return evidence

    def _persist(self, evidence: TestEvidence, *, session: Any = None, mode: str | None = None) -> TestEvidence:
        target = self.session if session is None else session
        if target is not None:
            outcome = "passed" if evidence.ok else "failed"
            error_code = None if evidence.ok else (evidence.error_code or evidence.verification_status)
            target.append("test_profile_result", evidence.to_dict(), mode=self.mode if mode is None else mode, outcome=outcome, error_code=error_code)
        return evidence


def list_test_profiles(workspace: Path, *, path: Path | None = None) -> TestProfiles:
    """Convenience API used by CLI ``test list``/``test show`` commands."""
    return TestProfileLoader(workspace, path).load()


def load_test_profiles(workspace: Path, *, path: Path | None = None) -> TestProfiles:
    """Explicitly named alias for integrations that prefer a loader verb."""
    return list_test_profiles(workspace, path=path)


def run_test_profile(
    workspace: Path,
    profile: TestProfile,
    *,
    approval: Any = None,
    session: Any = None,
    mode: str = "act",
    cancel: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
    secrets: Iterable[str] = (),
) -> TestEvidence:
    """One-call service API for ``test run`` and slash-command adapters."""
    return TestProfileRunner(WorkspaceGuard(workspace), approval=approval, session=session, mode=mode, secrets=secrets).run(
        profile,
        cancel=cancel,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "ExpectedExit",
    "OutputQuota",
    "MAX_TEST_PROFILE_BYTES",
    "TEST_PROFILE_SCHEMA_VERSION",
    "TestEvidence",
    "TestProfile",
    "TestProfileError",
    "TestProfileLoader",
    "TestProfileRunner",
    "TestProfiles",
    "list_test_profiles",
    "load_test_profiles",
    "run_test_profile",
    "workspace_fingerprint",
]
