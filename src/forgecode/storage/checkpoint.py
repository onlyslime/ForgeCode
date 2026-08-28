"""Safe run checkpoints and resume conflict detection.

Checkpoints describe what happened; they are never an instruction stream. A
resume caller must re-preview and re-approve any pending side effect.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from ..security.redaction import redact_text, redact_value
from ..security.json import bounded_json_loads
from ..security.workspace import WorkspaceGuard, WorkspaceViolation, assert_no_path_alias
from .session import bounded


CHECKPOINT_SCHEMA_VERSION = 1
MAX_CHECKPOINT_CHARS = 200_000


@dataclass(frozen=True)
class FileFingerprint:
    path: str
    exists: bool
    sha256: str | None = None
    size: int = 0
    mtime_ns: int = 0

    @classmethod
    def capture(cls, guard: WorkspaceGuard, path: str | Path) -> "FileFingerprint":
        target = guard.resolve(path)
        lexical = path if isinstance(path, Path) else Path(path)
        if not lexical.is_absolute():
            lexical = guard.root / lexical
        if target != lexical.absolute():
            raise WorkspaceViolation("checkpoint path is a symlink or junction alias")
        relative = guard.relative(target)
        try:
            stat = target.stat()
        except FileNotFoundError:
            return cls(relative, False)
        if not target.is_file():
            return cls(relative, True, sha256=None, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(relative, True, digest.hexdigest(), stat.st_size, stat.st_mtime_ns)

    def matches(self, guard: WorkspaceGuard) -> bool:
        current = self.capture(guard, self.path)
        return current == self

    def validate(self) -> None:
        if not _canonical_relative_path(self.path):
            raise ValueError("checkpoint file path is invalid")
        if not isinstance(self.exists, bool):
            raise ValueError("checkpoint file exists flag must be boolean")
        if self.sha256 is not None and (not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)):
            raise ValueError("checkpoint file sha256 is invalid")
        for name, value in (("size", self.size), ("mtime_ns", self.mtime_ns)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"checkpoint file {name} must be non-negative")


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    state: str
    mode: str
    workspace: str
    workspace_identity: str
    sequence: int
    files: tuple[FileFingerprint, ...] = ()
    last_tool_call: dict[str, Any] | None = None
    pending_actions: tuple[dict[str, Any], ...] = ()
    approvals: tuple[dict[str, Any], ...] = ()
    verification: dict[str, Any] | None = None
    context_summary: str = ""
    rules_fingerprint: str = ""
    plan_fingerprint: str = ""
    config_fingerprint: str = ""
    parent_run_id: str | None = None
    parent_sequence: int | None = None
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"unsupported checkpoint schema: {self.schema_version!r}")
        if not isinstance(self.run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.run_id):
            raise ValueError("checkpoint run_id is invalid")
        if self.state not in {"created", "discovering", "planning", "awaiting_approval", "acting", "verifying", "paused", "completed", "failed", "cancelled", "recovery_required"}:
            raise ValueError("checkpoint state is invalid")
        if self.mode not in {"plan", "act"}:
            raise ValueError("checkpoint mode is invalid")
        if self.workspace != "." or not isinstance(self.workspace_identity, str) or not re.fullmatch(r"[0-9a-f]{32}", self.workspace_identity):
            raise ValueError("checkpoint workspace identity is invalid")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("checkpoint sequence must be a non-negative integer")
        if not isinstance(self.files, tuple) or len(self.files) > 256:
            raise ValueError("checkpoint files are too large")
        for fingerprint in self.files:
            if not isinstance(fingerprint, FileFingerprint):
                raise ValueError("checkpoint files must be fingerprints")
            fingerprint.validate()
        for name, value, limit in (("pending_actions", self.pending_actions, 256), ("approvals", self.approvals, 256)):
            if not isinstance(value, tuple) or len(value) > limit or any(not isinstance(item, dict) for item in value):
                raise ValueError(f"checkpoint {name} are invalid")
        if self.last_tool_call is not None and not isinstance(self.last_tool_call, dict):
            raise ValueError("checkpoint last_tool_call must be an object or null")
        if self.verification is not None and not isinstance(self.verification, dict):
            raise ValueError("checkpoint verification must be an object or null")
        if not isinstance(self.context_summary, str) or len(self.context_summary) > 8_000:
            raise ValueError("checkpoint context summary is oversized")
        for name, value in (("rules_fingerprint", self.rules_fingerprint), ("plan_fingerprint", self.plan_fingerprint), ("config_fingerprint", self.config_fingerprint)):
            if not isinstance(value, str) or len(value) > 128:
                raise ValueError(f"checkpoint {name} is invalid")
        if self.parent_run_id is not None and (not isinstance(self.parent_run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.parent_run_id)):
            raise ValueError("checkpoint parent_run_id is invalid")
        if self.parent_sequence is not None and (isinstance(self.parent_sequence, bool) or not isinstance(self.parent_sequence, int) or self.parent_sequence < 0):
            raise ValueError("checkpoint parent_sequence is invalid")

    @classmethod
    def create(cls, guard: WorkspaceGuard, *, run_id: str, state: str, mode: str, sequence: int, files: Iterable[str | Path] = (), last_tool_call: dict[str, Any] | None = None, pending_actions: Iterable[dict[str, Any]] = (), approvals: Iterable[dict[str, Any]] = (), verification: dict[str, Any] | None = None, context_summary: str = "", rules_fingerprint: str = "", plan_fingerprint: str = "", config_fingerprint: str = "", parent_run_id: str | None = None, parent_sequence: int | None = None, secrets: Iterable[str] = ()) -> "Checkpoint":
        workspace_identity = hashlib.sha256(str(guard.root).encode("utf-8")).hexdigest()[:32]
        fingerprints = tuple(FileFingerprint.capture(guard, path) for path in files)
        return cls(run_id=run_id, state=str(state), mode=str(mode), workspace=".", workspace_identity=workspace_identity, sequence=sequence, files=fingerprints, last_tool_call=bounded(redact_value(last_tool_call, secrets)) if last_tool_call else None, pending_actions=tuple(bounded(redact_value(item, secrets)) for item in pending_actions), approvals=tuple(bounded(redact_value(item, secrets)) for item in approvals), verification=bounded(redact_value(verification, secrets)) if verification else None, context_summary=bounded(redact_text(context_summary, secrets), max_string_chars=8_000), rules_fingerprint=str(rules_fingerprint), plan_fingerprint=str(plan_fingerprint), config_fingerprint=str(config_fingerprint), parent_run_id=parent_run_id, parent_sequence=parent_sequence)


@dataclass(frozen=True)
class RecoveryConflict:
    path: str
    reason: str


class CheckpointStore:
    def __init__(self, path: Path, *, max_chars: int = MAX_CHECKPOINT_CHARS):
        try:
            self.path = assert_no_path_alias(Path(path), message="checkpoint path is a symlink or junction alias")
        except WorkspaceViolation as exc:
            raise ValueError(str(exc)) from exc
        if max_chars < 512:
            raise ValueError("checkpoint max_chars must be at least 512")
        self.max_chars = max_chars
        self._lock = threading.RLock()
        self._lock_local = threading.local()

    @contextmanager
    def _interprocess_lock(self):
        """Serialize checkpoint writers across threads and processes."""
        depth = getattr(self._lock_local, "depth", 0)
        if depth:
            self._lock_local.depth = depth + 1
            try:
                yield
            finally:
                self._lock_local.depth = depth
            return
        lock_path = Path(str(self.path) + ".lock")
        try:
            assert_no_path_alias(lock_path, message="checkpoint lock path is a symlink or junction alias")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+b")
        except (OSError, WorkspaceViolation) as exc:
            raise ValueError("cannot open checkpoint lock") from exc
        acquired = False
        try:
            deadline = time.monotonic() + 10.0
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                while not acquired:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ValueError("timed out waiting for checkpoint lock")
                        time.sleep(0.01)
            else:
                import fcntl

                while not acquired:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ValueError("timed out waiting for checkpoint lock")
                        time.sleep(0.01)
            self._lock_local.depth = 1
            yield
        finally:
            self._lock_local.depth = 0
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()

    def save(self, checkpoint: Checkpoint) -> None:
        with self._lock, self._interprocess_lock():
            checkpoint.validate()
            try:
                assert_no_path_alias(self.path, message="checkpoint path is a symlink or junction alias")
            except WorkspaceViolation as exc:
                raise ValueError(str(exc)) from exc
            payload = bounded(asdict(checkpoint), max_string_chars=20_000, max_items=200)
            serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(serialized) > self.max_chars:
                raise ValueError("checkpoint exceeds configured size limit")
            # Compare-and-swap on the persisted sequence.  Equal sequence is
            # idempotent only when the complete record is unchanged; otherwise
            # a stale writer could replace newer lifecycle/pending-action data.
            if self.path.exists():
                try:
                    current = self.load()
                except (FileNotFoundError, ValueError) as exc:
                    raise ValueError("existing checkpoint is unreadable; refusing overwrite") from exc
                if checkpoint.sequence < current.sequence:
                    raise ValueError("checkpoint sequence is older than the persisted checkpoint")
                if checkpoint.sequence == current.sequence:
                    current_serialized = json.dumps(bounded(asdict(current), max_string_chars=20_000, max_items=200), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    if serialized != current_serialized:
                        raise ValueError("checkpoint sequence already contains a different record")
                    return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    descriptor = -1
                    stream.write(serialized)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    if descriptor >= 0:
                        os.close(descriptor)
                except OSError:
                    pass
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def load(self) -> Checkpoint:
        try:
            assert_no_path_alias(self.path, message="checkpoint path is a symlink or junction alias")
        except WorkspaceViolation as exc:
            raise ValueError(str(exc)) from exc
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        try:
            before_stat = self.path.stat()
        except OSError as exc:
            raise ValueError(f"invalid checkpoint: OSError: {type(exc).__name__}") from exc
        if before_stat.st_size > self.max_chars * 2:
            raise ValueError("checkpoint file exceeds configured size limit")
        try:
            raw_text = self.path.read_text(encoding="utf-8")
            after_stat = self.path.stat()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid checkpoint: {type(exc).__name__}: {exc}") from exc
        if (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0)) != (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0)):
            raise ValueError("checkpoint changed while it was read")
        try:
            assert_no_path_alias(self.path, message="checkpoint path is a symlink or junction alias")
        except WorkspaceViolation as exc:
            raise ValueError(str(exc)) from exc
        try:
            raw = bounded_json_loads(raw_text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON value: {value}")))
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid checkpoint: {type(exc).__name__}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("checkpoint must be an object")
        allowed = {"run_id", "state", "mode", "workspace", "workspace_identity", "sequence", "files", "last_tool_call", "pending_actions", "approvals", "verification", "context_summary", "rules_fingerprint", "plan_fingerprint", "config_fingerprint", "parent_run_id", "parent_sequence", "schema_version"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("checkpoint contains unknown fields: " + ", ".join(sorted(str(item) for item in unknown)))
        if raw.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"unsupported checkpoint schema: {raw.get('schema_version')!r}")
        try:
            files = tuple(FileFingerprint(**item) for item in raw.pop("files", []))
            raw["files"] = files
            raw["pending_actions"] = tuple(raw.get("pending_actions", ()))
            raw["approvals"] = tuple(raw.get("approvals", ()))
            checkpoint = Checkpoint(**raw)
            checkpoint.validate()
            return checkpoint
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid checkpoint fields: {exc}") from exc

    def validate(self, checkpoint: Checkpoint, guard: WorkspaceGuard, *, expected_run_id: str | None = None, rules_fingerprint: str | None = None, plan_fingerprint: str | None = None, config_fingerprint: str | None = None) -> tuple[RecoveryConflict, ...]:
        conflicts: list[RecoveryConflict] = []
        try:
            checkpoint.validate()
        except (TypeError, ValueError) as exc:
            conflicts.append(RecoveryConflict("<checkpoint>", f"checkpoint fields are invalid: {type(exc).__name__}"))
            return tuple(conflicts)
        if expected_run_id and checkpoint.run_id != expected_run_id:
            conflicts.append(RecoveryConflict("<run>", "run_id does not match requested session"))
        identity = hashlib.sha256(str(guard.root).encode("utf-8")).hexdigest()[:32]
        if checkpoint.workspace_identity != identity:
            conflicts.append(RecoveryConflict("<workspace>", "workspace identity does not match checkpoint"))
        for path, expected, current, label in (
            ("<rules>", checkpoint.rules_fingerprint, rules_fingerprint, "project rules"),
            ("<plan>", checkpoint.plan_fingerprint, plan_fingerprint, "structured plan"),
            ("<config>", checkpoint.config_fingerprint, config_fingerprint, "effective config"),
        ):
            if current is not None and expected and current != expected:
                conflicts.append(RecoveryConflict(path, f"{label} fingerprint changed since checkpoint"))
        for fingerprint in checkpoint.files:
            try:
                if not fingerprint.matches(guard):
                    conflicts.append(RecoveryConflict(fingerprint.path, "file fingerprint changed since checkpoint"))
            except (OSError, WorkspaceViolation, ValueError) as exc:
                conflicts.append(RecoveryConflict(fingerprint.path, f"cannot validate file: {type(exc).__name__}"))
        return tuple(conflicts)


__all__ = ["CHECKPOINT_SCHEMA_VERSION", "Checkpoint", "CheckpointStore", "FileFingerprint", "RecoveryConflict"]


def _canonical_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2_000 or "\x00" in value or "\\" in value or value.startswith("/") or value.endswith("/") or "//" in value:
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return not (len(parts[0]) >= 2 and parts[0][1] == ":")
