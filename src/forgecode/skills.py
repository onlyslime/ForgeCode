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
import tempfile
import threading
import time
import ctypes
import ctypes.wintypes as wintypes
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .security.json import bounded_json_loads
from .security.redaction import redact_text
from .security.workspace import WorkspaceGuard, WorkspaceViolation, assert_no_path_alias


MAX_SKILLS = 128
MAX_SKILL_BYTES = 256_000
MAX_DESCRIPTION_CHARS = 4_000
MAX_CONTENT_CHARS = 40_000
MAX_SKILL_INPUT_CHARS = 256_000
MAX_STATE_BYTES = 256_000
MAX_DISCOVERY_CANDIDATES = 4_096
SKILL_MANIFEST_SCHEMA_VERSION = 1
SKILL_STATE_SCHEMA_VERSION = 1
PROCESS_CLEANUP_GRACE_SECONDS = 1.0
_WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE = 0x00002000
_SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ALLOWED_FIELDS = {
    "schema_version", "id", "name", "version", "description", "entry", "entry_type", "side_effect", "approval",
    "timeout_seconds", "max_output_chars", "allowed_paths", "input_schema", "enabled", "cwd", "environment",
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
    cwd: str = "."
    environment: tuple[str, ...] = ()
    schema_version: int = SKILL_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Accept JSON-friendly lists when callers round-trip ``to_dict`` and
        # canonicalize them once at the trust boundary.
        if isinstance(self.allowed_paths, list):
            object.__setattr__(self, "allowed_paths", tuple(self.allowed_paths))
        if isinstance(self.environment, list):
            object.__setattr__(self, "environment", tuple(self.environment))
        if isinstance(self.input_schema, type(None)):
            object.__setattr__(self, "input_schema", {})
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise SkillError("skill schema_version must be an integer")
        if self.schema_version == 0:
            # v0 manifests had the same fields but no explicit schema marker;
            # normalize them in memory so downstream evidence is unambiguous.
            object.__setattr__(self, "schema_version", SKILL_MANIFEST_SCHEMA_VERSION)
        elif self.schema_version != SKILL_MANIFEST_SCHEMA_VERSION:
            raise SkillError(f"unsupported skill manifest schema_version: {self.schema_version}")
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
        if not isinstance(self.cwd, str) or not self.cwd or len(self.cwd) > 512 or "\\" in self.cwd or Path(self.cwd).is_absolute() or ".." in Path(self.cwd).parts:
            raise SkillError("skill cwd must be a relative path")
        if not isinstance(self.environment, tuple) or len(self.environment) > 64:
            raise SkillError("skill environment must be a bounded allow-list")
        if any(not isinstance(name, str) or not _ENV_NAME.fullmatch(name) or any(marker in name.upper() for marker in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE")) for name in self.environment):
            raise SkillError("skill environment contains an invalid or credential-bearing name")
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
        result["environment"] = list(self.environment)
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


class _WindowsJob:
    """Small Job Object wrapper used to contain executable-skill children.

    ``CREATE_NEW_PROCESS_GROUP`` is not sufficient on Windows: once the
    direct entry process exits, ``taskkill /T`` can no longer discover a child
    that inherited an output handle.  A Job Object with
    ``KILL_ON_JOB_CLOSE`` gives us an ownership boundary independent of the
    direct process lifetime.  The wrapper is intentionally private and only
    instantiated on Windows; non-Windows execution uses a process group.
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects are unavailable")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self.handle = handle
        self._closed = False
        info = self._extended_limit_information()
        info.BasicLimitInformation.LimitFlags = _WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self.handle,
            _WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject failed")

    @staticmethod
    def _extended_limit_information() -> Any:
        class _LargeInteger(ctypes.Union):
            _fields_ = [("QuadPart", ctypes.c_longlong)]

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", _LargeInteger),
                ("PerJobUserTimeLimit", _LargeInteger),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        return _ExtendedLimitInformation()

    def _configure_api(self) -> None:
        handle_type = wintypes.HANDLE
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = handle_type
        self._kernel32.SetInformationJobObject.argtypes = [handle_type, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [handle_type, handle_type]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [handle_type, wintypes.UINT]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [handle_type]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def assign(self, process: subprocess.Popen[Any]) -> None:
        if self._closed or not self._kernel32.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process._handle)):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def terminate(self) -> None:
        if not self._closed:
            self._kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if not getattr(self, "_closed", True):
            self._kernel32.CloseHandle(self.handle)
            self._closed = True

    def __enter__(self) -> "_WindowsJob":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _new_windows_job() -> _WindowsJob | None:
    """Return a containing Job Object, or ``None`` when unavailable."""
    if os.name != "nt":
        return None
    try:
        return _WindowsJob()
    except (OSError, AttributeError):
        # Some restricted hosts disallow nested Job Objects.  The caller
        # retains the process-group/taskkill fallback and reports unresolved
        # cleanup rather than treating containment as guaranteed.
        return None


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

    def execute(
        self,
        skill: Skill,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
        cancellation: Callable[[], bool] | None = None,
        deadline_monotonic: float | None = None,
    ) -> str:
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
        job: _WindowsJob | None = None
        process: subprocess.Popen[Any] | None = None
        input_thread: threading.Thread | None = None
        try:
            from .security.workspace import assert_no_path_alias
            assert_no_path_alias(entry)
        except WorkspaceViolation as exc:
            raise SkillError("skill entry is a symlink or junction alias") from exc
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded) > MAX_CONTENT_CHARS:
            raise SkillError("skill arguments exceed the safety limit")
        try:
            cwd = self.guard.resolve(manifest.cwd, must_exist=True)
            if not cwd.is_dir():
                raise SkillError("skill cwd is not a directory")
            assert_no_path_alias(cwd)
        except WorkspaceViolation as exc:
            raise SkillError("skill cwd is a symlink or junction alias") from exc
        except (OSError, ValueError) as exc:
            raise SkillError("skill cwd is outside the workspace") from exc
        entry_identity = self._identity(entry)
        cwd_identity = self._identity(cwd)
        # Explicit environment allow-list.  A tiny platform baseline keeps
        # child processes usable while credential-like names are always
        # excluded, even if a manifest was forged around validation.
        baseline = {name for name in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "PYTHONIOENCODING") if name in os.environ}
        allowed = baseline | set(manifest.environment)
        env = {
            name: value for name, value in os.environ.items()
            if name in allowed and not any(marker in name.upper() for marker in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE"))
        }
        command = [sys.executable, str(entry)] if manifest.entry_type == "python" else [str(entry)]
        started = time.monotonic()
        if cancellation is not None and cancellation():
            raise SkillError("skill execution cancelled")
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise SkillError("skill execution timed out")
        try:
            # Re-check the path identities immediately before spawning.  This
            # closes the common check/use race where an attacker swaps an entry
            # or cwd after validation.  A process can still race at the exact
            # syscall boundary; its bounded evidence is then treated as an
            # unresolved/failed invocation by the caller.
            if self._identity(entry) != entry_identity or self._identity(cwd) != cwd_identity:
                raise SkillError("skill entry or cwd changed during validation")
            effective_timeout = manifest.timeout_seconds
            if deadline_monotonic is not None:
                effective_timeout = min(effective_timeout, max(0.0, deadline_monotonic - time.monotonic()))
            if effective_timeout <= 0:
                raise SkillError("skill execution timed out")
            process_options: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            # On Windows a process group does not contain descendants after
            # the group leader exits.  Attach the process to a Job Object
            # before sending input (and therefore before normal skill code can
            # run) so descendants that inherit our output handles remain
            # owned by this invocation.  Some hosts disallow nested jobs; in
            # that case ``job`` is left ``None`` and the bounded fallback
            # below reports unresolved cleanup rather than hanging.
            job = _new_windows_job()
            process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, shell=False, **process_options)
            if job is not None:
                try:
                    job.assign(process)
                except (OSError, AttributeError, TypeError):
                    job.close()
                    job = None
            # Send the bounded request asynchronously.  A skill is allowed to
            # ignore stdin; writing synchronously here could block forever on
            # a full pipe before timeout/cancellation gets control.
            assert process.stdin is not None
            def send_input() -> None:
                try:
                    assert process is not None and process.stdin is not None
                    process.stdin.write(encoded)
                    process.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    # A skill may exit before consuming its request.  Its
                    # return code/output remain authoritative.
                    pass

            input_thread = threading.Thread(target=send_input, name="forgecode-skill-stdin", daemon=True)
            input_thread.start()
            deadline = time.monotonic() + effective_timeout
            while process.poll() is None:
                if cancellation is not None and cancellation():
                    self._terminate(process, job=job)
                    job = None
                    raise SkillError("skill execution cancelled")
                if time.monotonic() >= deadline:
                    self._terminate(process, job=job)
                    job = None
                    raise SkillError("skill execution timed out")
                time.sleep(0.01)
            # ``poll()`` only proves that the direct entry process exited. A
            # skill can spawn a child that inherits stdout/stderr and keeps
            # those pipes open, making an unbounded ``communicate()`` hang
            # forever.  Drain with a grace deadline and terminate the whole
            # process group before returning a bounded result.  A Job Object
            # is explicitly terminated even though the direct process has
            # exited; this is the case where ``taskkill /T`` cannot find an
            # orphaned descendant on Windows.
            if job is not None:
                job.terminate()
                job.close()
                job = None
            if input_thread is not None:
                input_thread.join(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
                if input_thread.is_alive():
                    try:
                        if process.stdin is not None:
                            process.stdin.close()
                    except OSError:
                        pass
            try:
                stdout, stderr = process.communicate(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
            except subprocess.TimeoutExpired as exc:
                self._terminate(process, job=job)
                job = None
                partial_stdout = getattr(exc, "stdout", None) or ""
                partial_stderr = getattr(exc, "stderr", None) or ""
                try:
                    stdout, stderr = process.communicate(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
                except (OSError, subprocess.TimeoutExpired) as second:
                    stdout = partial_stdout or getattr(second, "stdout", None) or ""
                    stderr = partial_stderr or getattr(second, "stderr", None) or ""
                    for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
                        try:
                            if stream is not None:
                                stream.close()
                        except OSError:
                            pass
                    raise SkillError("skill output stream did not close after termination") from second
            completed_returncode = process.returncode
        except SkillError:
            raise
        except OSError as exc:
            raise SkillError(f"skill could not start: {type(exc).__name__}") from exc
        finally:
            if job is not None:
                job.terminate()
                job.close()
            if input_thread is not None and input_thread.is_alive():
                input_thread.join(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
                if input_thread.is_alive() and process is not None:
                    try:
                        if process.stdin is not None:
                            process.stdin.close()
                    except OSError:
                        pass
        output = (stdout or "") + (("\n[stderr]\n" + stderr) if stderr else "")
        output = redact_text(output)[: self.max_output_chars]
        if completed_returncode != 0:
            raise SkillError(f"skill exited with code {completed_returncode}: {output[:500]}")
        return output

    @staticmethod
    def _identity(path: Path) -> tuple[int, int, int, bool]:
        stat = path.stat()
        return (stat.st_dev, getattr(stat, "st_ino", 0), stat.st_mtime_ns, path.is_file())

    @staticmethod
    def _terminate(process: subprocess.Popen[Any], *, job: _WindowsJob | None = None) -> None:
        # Skills run in an isolated process group/session so descendants cannot
        # outlive a timeout and retain inherited pipes or side effects.
        if job is not None:
            # Terminate before closing so this remains effective even when the
            # direct process has already exited but a descendant owns a pipe.
            job.terminate()
            job.close()
        if os.name == "nt":
            try:
                # ``taskkill /T`` is useful while the parent is alive.  It is
                # deliberately attempted even after ``poll()`` so hosts that
                # retain a process-tree record get a chance to clean up.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                import signal

                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith(("\"", "'")) and value[-1:] == value[0]:
        return value[1:-1]
    if value.startswith("[") or value.startswith("{"):
        try:
            return bounded_json_loads(value)
        except ValueError as exc:
            # Front matter is intentionally not a full YAML dependency.  Do
            # accept the common bounded YAML shorthand ``[PATH, HOME]`` for
            # allow-lists while rejecting arbitrary executable expressions.
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if not inner:
                    return []
                items = [item.strip() for item in inner.split(",")]
                if all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", item.strip("\"'")) for item in items):
                    return [item.strip("\"'") for item in items]
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
            data = bounded_json_loads(raw)
        except ValueError as exc:
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
    values.setdefault("schema_version", SKILL_MANIFEST_SCHEMA_VERSION)
    # Schema v0 predates the explicit marker and is intentionally migrated in
    # memory; future versions fail closed in SkillManifest.__post_init__.
    if values.get("schema_version") == 0:
        values["schema_version"] = SKILL_MANIFEST_SCHEMA_VERSION
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
    values.setdefault("cwd", ".")
    values.setdefault("environment", ())
    if isinstance(values.get("allowed_paths"), list):
        values["allowed_paths"] = tuple(values["allowed_paths"])
    if isinstance(values.get("environment"), list):
        values["environment"] = tuple(values["environment"])
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
        self.diagnostics: list[str] = []
        self._cache_generation = 0

    def discover(self) -> tuple[Skill, ...]:
        self.errors = []
        self.diagnostics = []
        self._cache_generation += 1
        files: list[tuple[int, str, Path]] = []
        for directory_index, directory in enumerate(self.directories):
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
                discovered = list(root.rglob("*.md")) + list(root.rglob("*.json"))
            except OSError as exc:
                self.errors.append(f"{directory}: {type(exc).__name__}")
                continue
            # Resolve/sort each candidate independently.  A symlink or
            # junction race in one extension must become a diagnostic rather
            # than aborting all other skills in the directory.
            safe_files: list[tuple[str, Path]] = []
            for path in discovered:
                if path.suffix.lower() == ".json" and not path.name.endswith(".skill.json"):
                    continue
                try:
                    relative = self.guard.relative(path)
                    if path.is_file():
                        safe_files.append((relative.lower(), path))
                except (OSError, ValueError) as exc:
                    self.errors.append(f"{str(path)[:200]}: {type(exc).__name__}")
            files.extend((directory_index, relative, path) for relative, path in sorted(safe_files, key=lambda pair: pair[0]))
        # Directory order is the explicit precedence contract.  Within one
        # directory, canonical relative paths provide deterministic selection
        # independent of filesystem enumeration order.
        files.sort(key=lambda item: (item[0], item[1].lower(), item[1]))
        candidate_limit = MAX_DISCOVERY_CANDIDATES
        if len(files) > candidate_limit:
            self.errors.append(f"skill candidate count exceeds safety limit ({candidate_limit})")
            files = files[:candidate_limit]
        skills: list[Skill] = []
        seen: set[str] = set()
        selected_by_id: dict[str, str] = {}
        for directory_index, _, path in files:
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
                    raw = bounded_json_loads(text)
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
                original_schema = raw.get("schema_version", SKILL_MANIFEST_SCHEMA_VERSION)
                manifest = _manifest_from(raw, default_id=default_id[:64], content=content)
                if manifest.entry_type != "markdown":
                    try:
                        cwd = self.guard.resolve(manifest.cwd, must_exist=True)
                        if not cwd.is_dir():
                            raise SkillError("skill cwd must refer to a directory")
                        assert_no_path_alias(cwd)
                    except WorkspaceViolation as exc:
                        raise SkillError("skill cwd is a symlink or junction alias") from exc
                    except (OSError, ValueError) as exc:
                        raise SkillError("skill cwd is outside or missing from the workspace") from exc
                if manifest.id in seen:
                    winner = selected_by_id[manifest.id]
                    # Earlier explicitly configured directories win.  A
                    # shadowed extension is diagnosed but cannot make a
                    # valid higher-priority skill disappear.
                    current_relative = self.guard.relative(path)
                    same_directory = Path(current_relative).parent == Path(winner).parent
                    label = "manifest conflict" if same_directory else "shadowed skill"
                    self.diagnostics.append(f"{label} for skill id {manifest.id}: {current_relative} (winner: {winner})")
                    continue
                seen.add(manifest.id)
                selected_by_id[manifest.id] = relative
                diagnostics: list[str] = []
                if original_schema == 0:
                    diagnostics.append("migrated manifest schema 0 -> 1")
                    self.diagnostics.append(f"migrated manifest schema 0 -> 1: {relative}")
                skills.append(Skill(manifest, relative, redact_text(content)[:MAX_CONTENT_CHARS], tuple(diagnostics)))
            except (OSError, UnicodeError, json.JSONDecodeError, SkillError, ValueError) as exc:
                try:
                    display_path = self.guard.relative(path)
                except (OSError, ValueError):
                    display_path = str(path)[:200]
                self.errors.append(f"{display_path}: {type(exc).__name__}: {str(exc)[:200]}")
        return tuple(skills)

    def clear_cache(self) -> None:
        """Invalidate discovery diagnostics/cache state deterministically."""
        self._cache_generation += 1
        self.errors = []
        self.diagnostics = []


class SkillRegistry:
    """In-process skill view with optional durable enablement state.

    Source manifests remain immutable.  ``enable``/``disable``/``remove``
    change only this view unless a caller explicitly asks for persistence via
    ``persist=True`` or supplies ``state_path`` at construction.  The state
    file contains ids only (never prompt content or executable paths), is
    bounded and atomically replaced, and can therefore be safely shared by a
    later process that rediscovers the source skills.
    """

    def __init__(self, skills: Iterable[Skill] = (), *, state_path: Path | str | None = None, guard: WorkspaceGuard | None = None):
        self._skills = {skill.manifest.id: skill for skill in skills}
        self._guard = guard
        self._state_path: Path | None = None
        self._enabled_overrides: set[str] = set()
        self._disabled_overrides: set[str] = set()
        self._removed: set[str] = set()
        self.state_diagnostics: list[str] = []
        if state_path is not None:
            self._state_path = self._resolve_state_path(state_path)
            self.load_state()

    def _resolve_state_path(self, path: Path | str) -> Path:
        candidate = Path(path)
        if self._guard is not None:
            try:
                candidate = self._guard.resolve(candidate)
                if candidate == self._guard.root or candidate.suffix.lower() != ".json":
                    raise SkillError("skill state path must be a JSON file inside the workspace")
                assert_no_path_alias(candidate)
            except (OSError, ValueError, WorkspaceViolation) as exc:
                raise SkillError("skill state path is outside or aliases the workspace") from exc
        elif candidate.suffix.lower() != ".json" or candidate.name.startswith(".") and candidate.name == ".json":
            raise SkillError("skill state path must be a JSON file")
        return candidate

    @staticmethod
    def _validate_state_ids(value: Any, field_name: str) -> set[str]:
        if not isinstance(value, list) or len(value) > MAX_SKILLS:
            raise SkillError(f"skill state {field_name} must be a bounded array")
        result: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not _ID.fullmatch(item):
                raise SkillError(f"skill state {field_name} contains an invalid id")
            result.add(item)
        return result

    def _state_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SKILL_STATE_SCHEMA_VERSION,
            "enabled": sorted(self._enabled_overrides),
            "disabled": sorted(self._disabled_overrides),
            "removed": sorted(self._removed),
        }

    def save_state(self, path: Path | str | None = None) -> Path:
        """Persist only lifecycle ids using a bounded atomic JSON write."""
        destination = self._resolve_state_path(path or self._state_path or Path(".forgecode") / "skill-state.json")
        payload = self._state_payload()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise SkillError("skill state exceeds the safety limit")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if self._guard is not None:
                assert_no_path_alias(destination.parent)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                if self._guard is not None:
                    assert_no_path_alias(destination)
                os.replace(temporary_name, destination)
            finally:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
        except (OSError, WorkspaceViolation) as exc:
            raise SkillError(f"could not persist skill state: {type(exc).__name__}") from exc
        self._state_path = destination
        return destination

    def load_state(self, path: Path | str | None = None) -> tuple[str, ...]:
        """Load lifecycle overrides; unknown ids are retained as diagnostics."""
        source = self._resolve_state_path(path or self._state_path or Path(".forgecode") / "skill-state.json")
        self.state_diagnostics = []
        if not source.exists():
            self._state_path = source
            return ()
        try:
            if self._guard is not None:
                assert_no_path_alias(source)
            if not source.is_file() or source.stat().st_size > MAX_STATE_BYTES:
                raise SkillError("skill state is not a regular file or exceeds the size limit")
            raw = bounded_json_loads(source.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(SkillError(f"non-finite state value: {value}")))
            if not isinstance(raw, dict) or set(raw) != {"schema_version", "enabled", "disabled", "removed"}:
                raise SkillError("skill state has an unsupported shape")
            if raw.get("schema_version") != SKILL_STATE_SCHEMA_VERSION:
                raise SkillError("unsupported skill state schema")
            enabled = self._validate_state_ids(raw.get("enabled"), "enabled")
            disabled = self._validate_state_ids(raw.get("disabled"), "disabled")
            removed = self._validate_state_ids(raw.get("removed"), "removed")
            if enabled & disabled or (enabled | disabled) & removed:
                raise SkillError("skill state contains conflicting lifecycle ids")
        except SkillError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise SkillError(f"invalid skill state: {type(exc).__name__}") from exc
        self._enabled_overrides, self._disabled_overrides, self._removed = enabled, disabled, removed
        for skill_id in sorted(removed):
            if skill_id in self._skills:
                del self._skills[skill_id]
        for skill_id, skill in tuple(self._skills.items()):
            if skill_id in disabled:
                self._skills[skill_id] = Skill(replace(skill.manifest, enabled=False), skill.path, skill.content, skill.diagnostics)
            elif skill_id in enabled:
                self._skills[skill_id] = Skill(replace(skill.manifest, enabled=True), skill.path, skill.content, skill.diagnostics)
        unknown = (enabled | disabled | removed) - set(self._skills)
        self.state_diagnostics.extend(f"state references unknown skill id: {skill_id}" for skill_id in sorted(unknown))
        self._state_path = source
        return tuple(self.state_diagnostics)

    def state(self) -> dict[str, Any]:
        """Return a bounded copy of the lifecycle state for machine clients."""
        return dict(self._state_payload())

    def list(self) -> tuple[Skill, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))

    def get(self, skill_id: str) -> Skill:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise SkillError(f"unknown skill: {skill_id}") from exc

    def enable(self, skill_id: str) -> bool:
        """Enable a discovered skill without mutating its source manifest."""
        skill = self.get(skill_id)
        if skill.manifest.enabled:
            changed = False
        else:
            changed = True
        self._skills[skill_id] = Skill(replace(skill.manifest, enabled=True), skill.path, skill.content, skill.diagnostics)
        self._disabled_overrides.discard(skill_id)
        self._enabled_overrides.add(skill_id)
        if self._state_path is not None:
            self.save_state()
        return changed

    def disable(self, skill_id: str) -> bool:
        """Disable a skill; subsequent invocations fail closed locally."""
        skill = self.get(skill_id)
        if not skill.manifest.enabled:
            changed = False
        else:
            changed = True
        self._skills[skill_id] = Skill(replace(skill.manifest, enabled=False), skill.path, skill.content, skill.diagnostics)
        self._enabled_overrides.discard(skill_id)
        self._disabled_overrides.add(skill_id)
        if self._state_path is not None:
            self.save_state()
        return changed

    def remove(self, skill_id: str, *, persist: bool = False, state_path: Path | str | None = None) -> bool:
        """Remove a skill from the view; source files are never deleted.

        ``persist=True`` records the removal in the optional state file so a
        subsequent discovery cannot silently re-enable the extension.
        """
        if skill_id not in self._skills:
            return False
        del self._skills[skill_id]
        self._enabled_overrides.discard(skill_id)
        self._disabled_overrides.discard(skill_id)
        self._removed.add(skill_id)
        if persist or state_path is not None or self._state_path is not None:
            self.save_state(state_path)
        return True

    def restore(self, skill: Skill, *, persist: bool = False, state_path: Path | str | None = None) -> bool:
        """Restore a previously removed source skill explicitly."""
        skill_id = skill.manifest.id
        if skill_id in self._skills:
            return False
        self._removed.discard(skill_id)
        self._enabled_overrides.discard(skill_id)
        self._disabled_overrides.discard(skill_id)
        self._skills[skill_id] = skill
        if persist or state_path is not None or self._state_path is not None:
            self.save_state(state_path)
        return True

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
            detail = redact_text(str(exc))[:500]
            output = f"skill failed: {type(exc).__name__}" + (f": {detail}" if detail else "")
            return SkillInvocation(skill_id, manifest.version, False, output, type(exc).__name__, manifest.side_effect)


__all__ = ["MAX_SKILL_INPUT_CHARS", "Skill", "SkillError", "SkillExecutor", "SkillInvocation", "SkillLoader", "SkillManifest", "SkillRegistry", "SKILL_MANIFEST_SCHEMA_VERSION"]
