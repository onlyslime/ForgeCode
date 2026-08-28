"""Persistent transaction ledger and hash-checked undo.

Manifests contain bounded metadata only.  Exact before bytes are content-
addressed under the ignored runtime directory and are never returned by
normal review/export APIs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Any, Iterable

from ..security.workspace import WorkspaceGuard, WorkspaceViolation

TRANSACTION_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1_000_000
MAX_PREVIEW_CHARS = 20_000


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


class TransactionError(ValueError):
    """Transaction data is missing, corrupt, unsafe, or conflicting."""


@dataclass(frozen=True)
class TransactionOperation:
    path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    before_bytes: int
    after_bytes: int
    backup_sha256: str | None = None
    mode: int | None = None
    encoding: str = "utf-8"
    newline: str = "\n"

    def validate(self) -> None:
        if not _is_canonical_relative_path(self.path):
            raise TransactionError("transaction operation has an invalid path")
        if self.operation not in {"create", "update", "delete", "undo_create", "undo_update", "undo_delete"}:
            raise TransactionError(f"unknown transaction operation: {self.operation}")
        for name, digest in (("before_sha256", self.before_sha256), ("after_sha256", self.after_sha256), ("backup_sha256", self.backup_sha256)):
            if digest is not None and (not isinstance(digest, str) or not _is_digest(digest)):
                raise TransactionError(f"{name} is not a SHA-256 digest")
        for name, value in (("before_bytes", self.before_bytes), ("after_bytes", self.after_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000_000:
                raise TransactionError(f"{name} must be a bounded non-negative integer")
        if self.mode is not None and (isinstance(self.mode, bool) or not isinstance(self.mode, int) or self.mode < 0):
            raise TransactionError("mode must be a non-negative integer")
        if not isinstance(self.encoding, str) or not self.encoding or len(self.encoding) > 128:
            raise TransactionError("encoding is invalid")
        if self.encoding.lower().replace("_", "-") not in {"utf-8", "utf-8-sig"}:
            raise TransactionError("encoding is unsupported")
        if self.newline not in {"", "\n", "\r\n"}:
            raise TransactionError("newline is invalid")
        if self.operation in {"create", "update", "delete"} and self.before_sha256 != self.backup_sha256:
            raise TransactionError("backup_sha256 must match before_sha256")
        if self.operation in {"create", "undo_delete"} and (self.before_sha256 is not None or self.before_bytes != 0 or self.after_sha256 is None):
            raise TransactionError(f"{self.operation} hashes/sizes are inconsistent")
        if self.operation in {"delete", "undo_create"} and (self.before_sha256 is None or self.after_sha256 is not None or self.after_bytes != 0):
            raise TransactionError(f"{self.operation} hashes/sizes are inconsistent")
        if self.operation in {"update", "undo_update"} and (self.before_sha256 is None or self.after_sha256 is None):
            raise TransactionError(f"{self.operation} requires before and after hashes")


@dataclass(frozen=True)
class TransactionManifest:
    transaction_id: str
    run_id: str
    created_at: str
    tool: str
    state: str
    operations: tuple[TransactionOperation, ...]
    preview: str = ""
    approval: str = "approved"
    plan_id: str | None = None
    plan_item_id: str | None = None
    verification: dict[str, Any] | None = None
    parent_transaction_id: str | None = None
    rolled_back_by: str | None = None
    error: str | None = None
    schema_version: int = TRANSACTION_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != TRANSACTION_SCHEMA_VERSION:
            raise TransactionError(f"unsupported transaction schema: {self.schema_version}")
        if not isinstance(self.transaction_id, str) or not re_safe_id(self.transaction_id):
            raise TransactionError("transaction_id is invalid")
        if not isinstance(self.run_id, str) or not re_safe_id(self.run_id):
            raise TransactionError("run_id is invalid")
        if not isinstance(self.created_at, str) or not self.created_at or len(self.created_at) > 128:
            raise TransactionError("created_at is invalid")
        try:
            created = datetime.fromisoformat(self.created_at)
        except ValueError as exc:
            raise TransactionError("created_at is invalid") from exc
        if created.tzinfo is None or created.utcoffset() is None:
            raise TransactionError("created_at must include a timezone")
        if not isinstance(self.tool, str) or not self.tool or len(self.tool) > 128:
            raise TransactionError("tool is invalid")
        if self.state not in {"prepared", "committed", "failed", "undone", "recovery_required"}:
            raise TransactionError(f"invalid transaction state: {self.state}")
        if not self.operations or len(self.operations) > 256:
            raise TransactionError("transaction must contain 1-256 operations")
        for operation in self.operations:
            operation.validate()
        if not isinstance(self.preview, str) or not isinstance(self.approval, str):
            raise TransactionError("transaction preview and approval must be strings")
        if len(self.preview) > MAX_PREVIEW_CHARS + 128:
            raise TransactionError("transaction preview is oversized")
        for name, value in (("plan_id", self.plan_id), ("plan_item_id", self.plan_item_id), ("parent_transaction_id", self.parent_transaction_id), ("rolled_back_by", self.rolled_back_by)):
            if value is not None and (not isinstance(value, str) or len(value) > 128 or (name != "plan_item_id" and not re_safe_id(value))):
                raise TransactionError(f"{name} is invalid")
        if self.error is not None and (not isinstance(self.error, str) or len(self.error) > 1_000):
            raise TransactionError("transaction error is oversized")
        if self.verification is not None:
            if not isinstance(self.verification, dict):
                raise TransactionError("verification must be an object")
            encoded = json.dumps(self.verification, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 40_000:
                raise TransactionError("verification evidence exceeds size limit")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result = asdict(self)
        result["operations"] = [asdict(operation) for operation in self.operations]
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TransactionManifest":
        try:
            operations = tuple(TransactionOperation(**item) for item in raw["operations"])
            manifest = cls(**{**raw, "operations": operations})
        except (KeyError, TypeError, ValueError) as exc:
            raise TransactionError(f"invalid transaction manifest: {type(exc).__name__}") from exc
        manifest.validate()
        return manifest


@dataclass(frozen=True)
class UndoPreview:
    transaction_id: str
    available: bool
    conflicts: tuple[str, ...]
    preview: str
    operations: tuple[TransactionOperation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"transaction_id": self.transaction_id, "available": self.available, "conflicts": list(self.conflicts), "preview": self.preview, "operations": [asdict(item) for item in self.operations]}


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _is_canonical_relative_path(value: Any) -> bool:
    """Accept only the canonical POSIX paths written by WorkspaceGuard.

    A manifest is untrusted persisted data.  Absolute paths, Windows drive
    forms, alternate separators and dot traversal must not become aliases
    for another target during review or undo.
    """
    if not isinstance(value, str) or not value or len(value) > 2_000 or "\x00" in value or "\\" in value:
        return False
    if value.startswith("/") or value.endswith("/") or "//" in value:
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    first = parts[0]
    if len(first) >= 2 and first[1] == ":":
        return False
    return True


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".forgecode.tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, stat.S_IMODE(mode))
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            try: os.close(descriptor)
            except OSError: pass
        try:
            if temporary.exists(): temporary.unlink()
        except OSError:
            pass


class TransactionStore:
    def __init__(self, guard: WorkspaceGuard, root: Path | None = None, *, max_total_bytes: int = 50_000_000):
        self.guard = guard
        self.root = guard.resolve(root or Path(".forgecode") / "transactions")
        self.manifest_dir = self.root / "manifests"
        self.blob_dir = self.root / "blobs"
        if isinstance(max_total_bytes, bool) or not isinstance(max_total_bytes, int) or max_total_bytes < 1_024:
            raise ValueError("max_total_bytes must be an integer >= 1024")
        self.max_total_bytes = max_total_bytes
        if self.root == guard.root:
            raise TransactionError("transaction root cannot be workspace root")
        self._lock = threading.RLock()
        self.last_list_issues: tuple[str, ...] = ()

    def _manifest_path(self, transaction_id: str) -> Path:
        if not isinstance(transaction_id, str) or not re_safe_id(transaction_id):
            raise TransactionError("invalid transaction id")
        path = self.manifest_dir / f"{transaction_id}.json"
        return self.guard.resolve(path)

    def _blob_path(self, digest: str) -> Path:
        if not _is_digest(digest):
            raise TransactionError("invalid backup digest")
        return self.guard.resolve(self.blob_dir / digest)

    def _validate_runtime_dirs(self) -> None:
        """Validate runtime directories before enumeration or accounting.

        ``list`` and quota accounting cannot rely on ``Path.glob``/``os.walk``
        alone: both follow an alias when a runtime directory is replaced by a
        symlink or junction after store construction.  Keep the same
        WorkspaceGuard boundary used for individual manifest/blob paths.
        """
        for directory in (self.root, self.manifest_dir, self.blob_dir):
            try:
                resolved = self.guard.resolve(directory)
            except (OSError, ValueError, WorkspaceViolation) as exc:
                raise TransactionError("transaction runtime path is unsafe") from exc
            if resolved != directory.absolute():
                raise TransactionError("transaction runtime path is unsafe")
            if directory.exists() and not directory.is_dir():
                raise TransactionError("transaction runtime path is not a directory")

    def _runtime_bytes(self) -> int:
        self._validate_runtime_dirs()
        total = 0
        if self.root.exists():
            for directory, _names, files in os.walk(self.root, followlinks=False):
                for name in files:
                    try: total += (Path(directory) / name).stat().st_size
                    except OSError: continue
                    if total > self.max_total_bytes:
                        return total
        return total

    @staticmethod
    def _manifest_bytes(manifest: TransactionManifest) -> bytes:
        try:
            return json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            raise TransactionError(f"transaction manifest is not serializable: {type(exc).__name__}") from exc

    def prepare(self, *, transaction_id: str, run_id: str, tool: str, operations: Iterable[dict[str, Any]], before_bytes: dict[str, bytes | None], preview: str = "", plan_id: str | None = None, plan_item_id: str | None = None) -> TransactionManifest:
        with self._lock:
            if self._manifest_path(transaction_id).exists():
                raise TransactionError("transaction id already exists")
            if not isinstance(run_id, str) or not re_safe_id(run_id):
                raise TransactionError("run_id is invalid")
            if not isinstance(tool, str) or not tool or len(tool) > 128:
                raise TransactionError("tool is invalid")
            if not isinstance(before_bytes, dict):
                raise TransactionError("before_bytes must be an object")
            built: list[TransactionOperation] = []
            seen_paths: set[str] = set()
            pending_blobs: dict[str, bytes] = {}
            for raw in operations:
                if not isinstance(raw, dict):
                    raise TransactionError("transaction operation must be an object")
                path_value = raw.get("path")
                if not isinstance(path_value, str) or not path_value:
                    raise TransactionError("transaction operation path is invalid")
                resolved_path = self.guard.resolve(path_value)
                path = self.guard.relative(resolved_path)
                if path in seen_paths:
                    raise TransactionError(f"duplicate transaction operation path: {path}")
                seen_paths.add(path)
                data = before_bytes.get(path_value, before_bytes.get(path))
                if data is not None and not isinstance(data, bytes):
                    raise TransactionError(f"before bytes must be bytes or null: {path}")
                backup = _digest(data) if data is not None else None
                supplied_before = raw.get("before_sha256")
                if supplied_before is not None and supplied_before != backup:
                    raise TransactionError(f"before hash does not match captured bytes: {path}")
                try:
                    before_size = raw.get("before_bytes", len(data or b""))
                    after_size = raw.get("after_bytes", 0)
                    operation = TransactionOperation(path=path, operation=raw.get("operation"), before_sha256=backup, after_sha256=raw.get("after_sha256"), before_bytes=before_size, after_bytes=after_size, backup_sha256=backup, mode=raw.get("mode"), encoding=raw.get("encoding", "utf-8"), newline=raw.get("newline", "\n"))
                except (TypeError, ValueError) as exc:
                    raise TransactionError(f"invalid transaction operation: {type(exc).__name__}") from exc
                operation.validate()
                built.append(operation)
                if data is not None:
                    blob = self._blob_path(backup)
                    if blob.exists():
                        if not blob.is_file():
                            raise TransactionError(f"existing transaction blob is not a file: {backup}")
                        try:
                            existing = blob.read_bytes()
                        except OSError as exc:
                            raise TransactionError("existing transaction blob is unreadable") from exc
                        if _digest(existing) != backup:
                            raise TransactionError("existing transaction blob failed hash validation")
                    elif backup not in pending_blobs:
                        pending_blobs[backup] = data
            if not built:
                raise TransactionError("transaction must contain at least one operation")
            safe_preview = preview[:MAX_PREVIEW_CHARS] + ("\n[preview truncated]" if len(preview) > MAX_PREVIEW_CHARS else "") if isinstance(preview, str) else ""
            manifest = TransactionManifest(transaction_id, run_id, datetime.now(timezone.utc).isoformat(), tool, "prepared", tuple(built), safe_preview, "approved", plan_id, plan_item_id)
            manifest_bytes = self._manifest_bytes(manifest)
            if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise TransactionError("transaction manifest exceeds size limit")
            prospective = sum(len(data) for data in pending_blobs.values())
            if self._runtime_bytes() + prospective + len(manifest_bytes) > self.max_total_bytes:
                raise TransactionError("transaction backup store would exceed configured retention limit")
            created: list[tuple[Path, str]] = []
            try:
                for digest, data in pending_blobs.items():
                    blob = self._blob_path(digest)
                    _atomic_bytes(blob, data)
                    created.append((blob, digest))
                self._save(manifest)
            except Exception:
                # A failed manifest save must not strand newly-created backup
                # blobs.  Only remove files that still contain the bytes we
                # created; never delete a concurrent replacement.
                for blob, digest in reversed(created):
                    try:
                        if blob.is_file() and _digest(blob.read_bytes()) == digest:
                            blob.unlink()
                    except OSError:
                        pass
                raise
            return manifest

    def commit(self, transaction_id: str) -> TransactionManifest:
        manifest = self.load(transaction_id)
        if manifest.state != "prepared":
            raise TransactionError(f"cannot commit transaction in state {manifest.state}")
        for operation in manifest.operations:
            current = self._current_digest(operation.path)
            if current != operation.after_sha256:
                raise TransactionError(f"transaction after hash mismatch: {operation.path}")
        committed = replace(manifest, state="committed")
        self._save(committed)
        return committed

    def fail(self, transaction_id: str, error: str, *, recovery_required: bool = False) -> TransactionManifest:
        manifest = self.load(transaction_id)
        failed = replace(manifest, state="recovery_required" if recovery_required else "failed", error=str(error)[:1_000])
        self._save(failed)
        return failed

    def _save(self, manifest: TransactionManifest) -> None:
        data = self._manifest_bytes(manifest)
        if len(data) > MAX_MANIFEST_BYTES:
            raise TransactionError("transaction manifest exceeds size limit")
        _atomic_bytes(self._manifest_path(manifest.transaction_id), data)

    def load(self, transaction_id: str) -> TransactionManifest:
        path = self._manifest_path(transaction_id)
        try:
            before_stat = path.stat()
            raw_bytes = path.read_bytes()
            after_stat = path.stat()
            if len(raw_bytes) > MAX_MANIFEST_BYTES:
                raise TransactionError("transaction manifest exceeds size limit")
            if (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0)) != (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0)):
                raise TransactionError("transaction manifest changed while it was read")
            raw = json.loads(raw_bytes, parse_constant=_reject_nonfinite_json)
        except FileNotFoundError as exc:
            raise TransactionError(f"transaction not found: {transaction_id}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            detail = str(exc).replace("\r", " ").replace("\n", " ")[:200]
            raise TransactionError(f"transaction manifest is unreadable: {type(exc).__name__}: {detail}") from exc
        if not isinstance(raw, dict):
            raise TransactionError("transaction manifest must be an object")
        manifest = TransactionManifest.from_dict(raw)
        if manifest.transaction_id != transaction_id:
            raise TransactionError("transaction id does not match manifest filename")
        return manifest

    def list(self, *, limit: int = 100) -> tuple[TransactionManifest, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("transaction list limit must be 1-1000")
        self.last_list_issues = ()
        try:
            self._validate_runtime_dirs()
        except TransactionError as exc:
            self.last_list_issues = (str(exc),)
            return ()
        if not self.manifest_dir.is_dir():
            return ()
        manifests: list[TransactionManifest] = []
        issues: list[str] = []
        paths = []
        try:
            paths = list(self.manifest_dir.glob("*.json"))
        except OSError as exc:
            self.last_list_issues = (f"manifest directory is unreadable: {type(exc).__name__}",)
            return ()
        def mtime(path: Path) -> int:
            try:
                return path.stat().st_mtime_ns if path.is_file() else 0
            except OSError:
                return 0
        for path in sorted(paths, key=mtime, reverse=True):
            try:
                manifests.append(self.load(path.stem))
            except (OSError, TransactionError) as exc:
                issues.append(f"{path.name}: {type(exc).__name__}: {str(exc)[:240]}")
            if len(manifests) >= limit: break
        self.last_list_issues = tuple(issues[:100])
        return tuple(manifests)

    def latest(self, *, committed_only: bool = False) -> TransactionManifest:
        for manifest in self.list():
            if not committed_only or manifest.state == "committed":
                return manifest
        suffix = f"; corrupt manifests: {' | '.join(self.last_list_issues[:5])}" if self.last_list_issues else ""
        raise TransactionError("no matching transactions" + suffix)

    def review(self, transaction_id: str = "latest") -> dict[str, Any]:
        manifest = self.latest() if transaction_id == "latest" else self.load(transaction_id)
        if transaction_id != "latest":
            # Explicit review still reports unrelated corrupt ledger entries
            # without allowing them to hide the requested valid manifest.
            self.list(limit=1_000)
        preview = self.preview_undo(manifest.transaction_id)
        payload = manifest.to_dict()
        payload["rollback_available"] = preview.available
        payload["rollback_conflicts"] = list(preview.conflicts)
        payload["store_issues"] = list(self.last_list_issues)
        return payload

    def attach_verification(self, transaction_id: str, verification: dict[str, Any]) -> TransactionManifest:
        manifest = self.load(transaction_id)
        try:
            safe = json.loads(json.dumps(verification, ensure_ascii=False, default=str, allow_nan=False))
            encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise TransactionError(f"verification evidence is not serializable: {type(exc).__name__}") from exc
        if len(encoded) > 40_000:
            safe = {"truncated": True, "preview": encoded[:39_000]}
        updated = replace(manifest, verification=safe)
        self._save(updated)
        return updated

    def preview_undo(self, transaction_id: str = "latest") -> UndoPreview:
        manifest = self.latest(committed_only=True) if transaction_id == "latest" else self.load(transaction_id)
        conflicts: list[str] = []
        # v0.0.6 deliberately implements undo but not redo.  The ledger entry
        # created by an undo is evidence of the restore operation, not a new
        # user transaction that may itself be undone back into the original
        # side effect.
        if manifest.tool == "undo_transaction" or manifest.parent_transaction_id:
            conflicts.append("undo transactions are not undoable (redo is not supported)")
        if manifest.state != "committed":
            conflicts.append(f"transaction state is {manifest.state}, not committed")
        if manifest.rolled_back_by:
            conflicts.append(f"transaction was already undone by {manifest.rolled_back_by}")
        reverse: list[TransactionOperation] = []
        lines: list[str] = []
        for operation in manifest.operations:
            try:
                current = self._current_digest(operation.path)
            except (OSError, ValueError, WorkspaceViolation) as exc:
                conflicts.append(f"{operation.path}: current file unavailable ({type(exc).__name__})"); continue
            if current != operation.after_sha256:
                conflicts.append(f"{operation.path}: current hash differs from transaction after hash")
            if operation.backup_sha256:
                try: self._read_blob(operation.backup_sha256)
                except TransactionError as exc: conflicts.append(f"{operation.path}: {exc}")
            reverse_op = "undo_create" if operation.operation == "create" else ("undo_delete" if operation.operation == "delete" else "undo_update")
            reverse.append(TransactionOperation(operation.path, reverse_op, operation.after_sha256, operation.before_sha256, operation.after_bytes, operation.before_bytes, operation.backup_sha256, operation.mode, operation.encoding, operation.newline))
            lines.append(f"{operation.path}: {operation.operation} -> restore before sha256={operation.before_sha256 or '<absent>'}")
        preview = "\n".join(lines)[:MAX_PREVIEW_CHARS]
        return UndoPreview(manifest.transaction_id, not conflicts, tuple(conflicts), preview, tuple(reverse))

    def undo(self, transaction_id: str, *, approval: Any, run_id: str, plan_id: str | None = None, plan_item_id: str | None = None) -> TransactionManifest:
        preview = self.preview_undo(transaction_id)
        if not preview.available:
            raise TransactionError("undo conflict: " + "; ".join(preview.conflicts))
        original = self.load(preview.transaction_id)
        undo_id = hashlib.sha256(f"undo:{original.transaction_id}:{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:32]
        arguments = {"transaction_id": original.transaction_id, "undo_transaction_id": undo_id, "preview": preview.preview, "operations": [operation.path for operation in preview.operations]}
        if approval is None or not approval.approve("undo_transaction", arguments):
            raise TransactionError("undo denied by approval policy")
        # The preview may have been displayed to a human for an arbitrary
        # amount of time.  Re-check every optimistic-concurrency precondition
        # after approval and immediately before creating the undo ledger.  A
        # concurrent writer must never be overwritten merely because the
        # earlier preview was clean.
        latest = self.preview_undo(original.transaction_id)
        if not latest.available:
            raise TransactionError("undo conflict: " + "; ".join(latest.conflicts))
        preview = latest
        before_map: dict[str, bytes | None] = {}
        raw_operations: list[dict[str, Any]] = []
        target_bytes: dict[str, bytes | None] = {}
        for reverse in preview.operations:
            path = self.guard.resolve(reverse.path)
            current = path.read_bytes() if path.is_file() else None
            current_digest = _digest(current) if current is not None else None
            if current_digest != reverse.before_sha256:
                raise TransactionError(f"undo conflict: {reverse.path} changed after approval")
            before_map[reverse.path] = current
            data = self._read_blob(reverse.backup_sha256) if reverse.backup_sha256 else None
            target_bytes[reverse.path] = data
            raw_operations.append(asdict(reverse))
        undo_manifest = self.prepare(transaction_id=undo_id, run_id=run_id, tool="undo_transaction", operations=raw_operations, before_bytes=before_map, preview=preview.preview, plan_id=plan_id, plan_item_id=plan_item_id)
        written: list[tuple[Path, bytes | None, int | None]] = []
        try:
            for reverse in preview.operations:
                path = self.guard.resolve(reverse.path)
                old = path.read_bytes() if path.is_file() else None
                old_mode = path.stat().st_mode if path.exists() else None
                written.append((path, old, old_mode))
                data = target_bytes[reverse.path]
                if data is None:
                    if path.exists(): path.unlink()
                else:
                    _atomic_bytes(path, data, mode=reverse.mode)
            undo_manifest = self.commit(undo_id)
            undo_manifest = replace(undo_manifest, parent_transaction_id=original.transaction_id)
            self._save(undo_manifest)
            self._save(replace(original, state="undone", rolled_back_by=undo_id))
            return undo_manifest
        except Exception as exc:
            recovery_required = False
            for path, data, mode in reversed(written):
                try:
                    if data is None:
                        if path.exists(): path.unlink()
                    else: _atomic_bytes(path, data, mode=mode)
                except OSError:
                    recovery_required = True
            try: self.fail(undo_id, f"{type(exc).__name__}: {exc}", recovery_required=recovery_required)
            except Exception: pass
            raise TransactionError(f"undo failed: {type(exc).__name__}: {exc}") from exc

    def _current_digest(self, relative: str) -> str | None:
        path = self.guard.resolve(relative)
        if not path.exists(): return None
        if not path.is_file(): raise TransactionError(f"transaction path is not a file: {relative}")
        return _digest(path.read_bytes())

    def _read_blob(self, digest: str | None) -> bytes:
        if digest is None: raise TransactionError("backup blob is missing")
        try:
            path = self._blob_path(digest)
            before_stat = path.stat()
            data = path.read_bytes()
            after_stat = path.stat()
        except OSError as exc: raise TransactionError("backup blob is missing or unreadable") from exc
        if (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0)) != (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0)):
            raise TransactionError("backup blob changed while it was read")
        if _digest(data) != digest: raise TransactionError("backup blob failed hash validation")
        return data


def re_safe_id(value: str) -> bool:
    return value.isascii() and 1 <= len(value) <= 128 and all(character.isalnum() or character in "-_" for character in value)


__all__ = ["TRANSACTION_SCHEMA_VERSION", "TransactionError", "TransactionManifest", "TransactionOperation", "TransactionStore", "UndoPreview"]
