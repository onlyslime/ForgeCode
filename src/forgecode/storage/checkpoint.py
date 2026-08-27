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
import tempfile
from typing import Any, Iterable

from ..security.redaction import redact_text, redact_value
from ..security.workspace import WorkspaceGuard, WorkspaceViolation
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
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    @classmethod
    def create(cls, guard: WorkspaceGuard, *, run_id: str, state: str, mode: str, sequence: int, files: Iterable[str | Path] = (), last_tool_call: dict[str, Any] | None = None, pending_actions: Iterable[dict[str, Any]] = (), approvals: Iterable[dict[str, Any]] = (), verification: dict[str, Any] | None = None, context_summary: str = "", secrets: Iterable[str] = ()) -> "Checkpoint":
        workspace_identity = hashlib.sha256(str(guard.root).encode("utf-8")).hexdigest()[:32]
        fingerprints = tuple(FileFingerprint.capture(guard, path) for path in files)
        return cls(run_id=run_id, state=str(state), mode=str(mode), workspace=".", workspace_identity=workspace_identity, sequence=sequence, files=fingerprints, last_tool_call=bounded(redact_value(last_tool_call, secrets)) if last_tool_call else None, pending_actions=tuple(bounded(redact_value(item, secrets)) for item in pending_actions), approvals=tuple(bounded(redact_value(item, secrets)) for item in approvals), verification=bounded(redact_value(verification, secrets)) if verification else None, context_summary=bounded(redact_text(context_summary, secrets), max_string_chars=8_000))


@dataclass(frozen=True)
class RecoveryConflict:
    path: str
    reason: str


class CheckpointStore:
    def __init__(self, path: Path, *, max_chars: int = MAX_CHECKPOINT_CHARS):
        self.path = Path(path)
        if max_chars < 512:
            raise ValueError("checkpoint max_chars must be at least 512")
        self.max_chars = max_chars

    def save(self, checkpoint: Checkpoint) -> None:
        payload = bounded(asdict(checkpoint), max_string_chars=20_000, max_items=200)
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(serialized) > self.max_chars:
            raise ValueError("checkpoint exceeds configured size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink()
            except OSError:
                pass

    def load(self) -> Checkpoint:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if self.path.stat().st_size > self.max_chars * 2:
            raise ValueError("checkpoint file exceeds configured size limit")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid checkpoint: {type(exc).__name__}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("checkpoint must be an object")
        if raw.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"unsupported checkpoint schema: {raw.get('schema_version')!r}")
        try:
            files = tuple(FileFingerprint(**item) for item in raw.pop("files", []))
            raw["files"] = files
            raw["pending_actions"] = tuple(raw.get("pending_actions", ()))
            raw["approvals"] = tuple(raw.get("approvals", ()))
            return Checkpoint(**raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid checkpoint fields: {exc}") from exc

    def validate(self, checkpoint: Checkpoint, guard: WorkspaceGuard, *, expected_run_id: str | None = None) -> tuple[RecoveryConflict, ...]:
        conflicts: list[RecoveryConflict] = []
        if expected_run_id and checkpoint.run_id != expected_run_id:
            conflicts.append(RecoveryConflict("<run>", "run_id does not match requested session"))
        identity = hashlib.sha256(str(guard.root).encode("utf-8")).hexdigest()[:32]
        if checkpoint.workspace_identity != identity:
            conflicts.append(RecoveryConflict("<workspace>", "workspace identity does not match checkpoint"))
        for fingerprint in checkpoint.files:
            try:
                if not fingerprint.matches(guard):
                    conflicts.append(RecoveryConflict(fingerprint.path, "file fingerprint changed since checkpoint"))
            except (OSError, WorkspaceViolation, ValueError) as exc:
                conflicts.append(RecoveryConflict(fingerprint.path, f"cannot validate file: {type(exc).__name__}"))
        return tuple(conflicts)


__all__ = ["CHECKPOINT_SCHEMA_VERSION", "Checkpoint", "CheckpointStore", "FileFingerprint", "RecoveryConflict"]
