"""A small, deterministic, workspace-scoped patch tool.

The parser intentionally supports ordinary unified diffs and the familiar
``*** Begin Patch`` form, but it does not execute patch text as a command. All
files are read and transformed in memory before one approval decision and
failure-safe replacements are made.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import difflib
import os
import re
import tempfile
from typing import Any
import hashlib

from .base import PauseRequested, ToolContext, ToolDefinition, ToolResult


_MAX_PATCH_CHARS = 200_000
_MAX_PATCH_FILES = 32
_MAX_PATCH_HUNKS = 256
_MAX_PREVIEW_CHARS = 12_000
_MAX_TARGET_FILE_BYTES = 2_000_000


class _PatchDeadlineExceeded(RuntimeError):
    """Abort an apply_patch operation before the next filesystem mutation."""


@dataclass(frozen=True)
class PatchHunk:
    old_start: int | None
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    old_count: int | None = None
    new_count: int | None = None


@dataclass(frozen=True)
class PatchOperation:
    path: str
    hunks: tuple[PatchHunk, ...]
    create: bool = False
    delete: bool = False


@dataclass(frozen=True)
class ChangeOperation:
    """Auditable description of one planned filesystem operation."""

    path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    before_bytes: int
    after_bytes: int
    encoding: str = "utf-8"
    newline: str = "\\n"
    outcome: str = "planned"


@dataclass(frozen=True)
class ChangePlan:
    transaction_id: str
    operations: tuple[ChangeOperation, ...]
    preview: str
    approval: str = "pending"


@dataclass(frozen=True)
class ChangeResult:
    transaction_id: str
    ok: bool
    operations: tuple[ChangeOperation, ...]
    rolled_back: bool = False
    error: str | None = None


class PatchFormatError(ValueError):
    pass


_HUNK_RE = re.compile(r"^@@\s+-([0-9]+)(?:,([0-9]+))?\s+\+([0-9]+)(?:,([0-9]+))?\s+@@")


def _clean_path(value: str) -> str:
    value = value.strip().split("\t", 1)[0].strip()
    if value in {"/dev/null", "dev/null"}:
        return value
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def _parse_hunk(
    lines: list[str],
    start: int,
    *,
    old_start: int | None = None,
    expected_old: int | None = None,
    expected_new: int | None = None,
) -> tuple[PatchHunk, int]:
    old: list[str] = []
    new: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if expected_old is not None and len(old) >= expected_old and len(new) >= (expected_new or 0):
            # Git emits this marker after a hunk whose source or destination
            # lacks a final newline. It is metadata, not another hunk line.
            while index < len(lines) and lines[index] == r"\ No newline at end of file":
                index += 1
            break
        if expected_old is None and (line.startswith("@@") or line.startswith("*** ") or line.startswith("--- ")):
            break
        if line == r"\ No newline at end of file":
            index += 1
            continue
        if not line:
            raise PatchFormatError("patch hunk contains an unprefixed blank line")
        marker, text = line[0], line[1:]
        if marker == " ":
            old.append(text)
            new.append(text)
        elif marker == "-":
            old.append(text)
        elif marker == "+":
            new.append(text)
        else:
            raise PatchFormatError(f"invalid patch hunk line: {line[:80]!r}")
        index += 1
    if not old and not new:
        raise PatchFormatError("patch hunk is empty")
    return PatchHunk(old_start, tuple(old), tuple(new)), index


def _parse_unified(lines: list[str]) -> list[PatchOperation]:
    operations: list[PatchOperation] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            raise PatchFormatError("unified patch must start each file with ---")
        old_path = _clean_path(lines[index][4:])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise PatchFormatError("unified patch is missing its +++ path")
        new_path = _clean_path(lines[index][4:])
        index += 1
        create = old_path == "/dev/null"
        delete = new_path == "/dev/null"
        path = new_path if not delete else old_path
        if path in {"/dev/null", "dev/null", ""}:
            raise PatchFormatError("patch file path is empty")
        hunks: list[PatchHunk] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            match = _HUNK_RE.match(lines[index])
            if not match:
                raise PatchFormatError(f"expected a hunk header, got {lines[index][:80]!r}")
            old_start = int(match.group(1))
            old_count = 1 if match.group(2) is None else int(match.group(2))
            new_count = 1 if match.group(4) is None else int(match.group(4))
            index += 1
            hunk, index = _parse_hunk(
                lines,
                index,
                old_start=old_start,
                expected_old=old_count,
                expected_new=new_count,
            )
            if len(hunk.old_lines) != old_count or len(hunk.new_lines) != new_count:
                raise PatchFormatError("patch hunk line counts do not match its header")
            hunk = PatchHunk(hunk.old_start, hunk.old_lines, hunk.new_lines, old_count, new_count)
            hunks.append(hunk)
        if not hunks:
            raise PatchFormatError("patch file has no hunks")
        operations.append(PatchOperation(path, tuple(hunks), create=create, delete=delete))
    return operations


def _parse_custom(lines: list[str]) -> list[PatchOperation]:
    operations: list[PatchOperation] = []
    index = 0
    if lines and lines[0].strip() == "*** Begin Patch":
        lines = lines[1:]
    if lines and lines[-1].strip() == "*** End Patch":
        lines = lines[:-1]
    while index < len(lines):
        header = lines[index]
        index += 1
        if header.startswith("*** Update File: "):
            path, create, delete = header[len("*** Update File: "):].strip(), False, False
        elif header.startswith("*** Add File: "):
            path, create, delete = header[len("*** Add File: "):].strip(), True, False
        elif header.startswith("*** Delete File: "):
            path, create, delete = header[len("*** Delete File: "):].strip(), False, True
        else:
            raise PatchFormatError(f"unknown patch file header: {header[:80]!r}")
        hunks: list[PatchHunk] = []
        while index < len(lines) and not lines[index].startswith("*** "):
            if not lines[index].startswith("@@"):
                raise PatchFormatError("custom patch requires @@ before each hunk")
            index += 1
            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)
        if not hunks and not delete:
            raise PatchFormatError("patch file has no hunks")
        operations.append(PatchOperation(path, tuple(hunks), create=create, delete=delete))
    return operations


def parse_patch(text: str) -> tuple[PatchOperation, ...]:
    if not isinstance(text, str) or not text.strip():
        raise PatchFormatError("patch must be a non-empty string")
    if len(text) > _MAX_PATCH_CHARS:
        raise PatchFormatError(f"patch exceeds the {_MAX_PATCH_CHARS}-character limit")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise PatchFormatError("patch must not be empty")
    if lines[0].startswith("*** Begin Patch") or any(
        line.startswith(("*** Update File: ", "*** Add File: ", "*** Delete File: ")) for line in lines
    ):
        operations = _parse_custom(lines)
    else:
        # `git diff` commonly prefixes the unified file headers with `diff`,
        # `index`, and mode metadata. Those lines carry no patch content and
        # are safe to ignore, while arbitrary prose is still rejected.
        first_file_header = next((index for index, line in enumerate(lines) if line.startswith("--- ")), None)
        if first_file_header is not None and first_file_header:
            preamble = lines[:first_file_header]
            if all(line.startswith(("diff --git ", "index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ")) for line in preamble):
                lines = lines[first_file_header:]
        operations = _parse_unified(lines)
    if len(operations) > _MAX_PATCH_FILES:
        raise PatchFormatError(f"patch contains more than {_MAX_PATCH_FILES} files")
    if sum(len(operation.hunks) for operation in operations) > _MAX_PATCH_HUNKS:
        raise PatchFormatError(f"patch contains more than {_MAX_PATCH_HUNKS} hunks")
    paths = [operation.path for operation in operations]
    if len(paths) != len(set(paths)):
        raise PatchFormatError("patch contains duplicate file paths")
    if not operations:
        raise PatchFormatError("patch contains no file operations")
    return tuple(operations)


def _apply_hunk(content: list[str], hunk: PatchHunk) -> None:
    old = list(hunk.old_lines)
    replacement = list(hunk.new_lines)
    if not old and hunk.old_start is None:
        position = len(content)
        content[position:position] = replacement
        return
    # For a zero-context insertion, unified-diff's old start denotes the
    # boundary *after* that many old lines (``-1,0`` inserts at position 1),
    # whereas a replacement hunk starts at the one-based line itself.
    if old:
        expected = max(0, (hunk.old_start or 1) - 1)
    else:
        expected = max(0, hunk.old_start or 0)
    matches = [position for position in range(0, len(content) + 1) if content[position:position + len(old)] == old]
    if not matches:
        raise PatchFormatError("patch context does not match the current file")
    if len(matches) > 1:
        nearest_distance = min(abs(position - expected) for position in matches)
        nearest = [position for position in matches if abs(position - expected) == nearest_distance]
        if len(nearest) != 1:
            raise PatchFormatError("patch context is ambiguous")
        position = nearest[0]
    else:
        position = matches[0]
    content[position:position + len(old)] = replacement


def _apply_operation(operation: PatchOperation, original: str | None) -> str | None:
    if operation.delete:
        if original is None:
            raise PatchFormatError(f"cannot delete missing file: {operation.path}")
        if not operation.hunks:
            return None
        content = original.splitlines()
        for hunk in operation.hunks:
            _apply_hunk(content, hunk)
        if content:
            raise PatchFormatError(f"delete patch did not remove all content: {operation.path}")
        return None
    if operation.create:
        if original is not None:
            raise PatchFormatError(f"cannot create an existing file: {operation.path}")
        content: list[str] = []
    else:
        if original is None:
            raise PatchFormatError(f"cannot update missing file: {operation.path}")
        content = original.splitlines()
    for hunk in operation.hunks:
        _apply_hunk(content, hunk)
    newline = "\r\n" if original is not None and "\r\n" in original else "\n"
    had_trailing_newline = original is None or original.endswith(("\n", "\r"))
    result = newline.join(content)
    if had_trailing_newline and (content or (original and original.endswith(("\n", "\r")))):
        result += newline
    return result


def _atomic_write(path, content: str) -> None:
    original_mode = None
    try:
        original_mode = path.stat().st_mode
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".forgecode.patch.tmp", dir=path.parent)
    try:
        temporary = type(path)(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if original_mode is not None:
            os.chmod(temporary, original_mode)
        os.replace(temporary, path)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except (UnboundLocalError, FileNotFoundError, OSError):
            pass


class ApplyPatchTool:
    definition = ToolDefinition(
        "apply_patch",
        "Apply a validated unified diff to UTF-8 workspace files after approval.",
        {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "maxLength": _MAX_PATCH_CHARS},
                "allow_delete": {"type": "boolean"},
            },
            "required": ["patch"],
        },
        side_effecting=True,
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        patch_text = arguments.get("patch")
        if not isinstance(patch_text, str):
            raise ValueError("patch must be a string")
        allow_delete = arguments.get("allow_delete", False)
        if not isinstance(allow_delete, bool):
            raise ValueError("allow_delete must be a boolean")
        try:
            operations = parse_patch(patch_text)
            if any(operation.delete for operation in operations) and not allow_delete:
                return ToolResult(False, "delete patches require allow_delete=true", {"error": "delete_requires_explicit_flag"})
            planned: list[tuple[PatchOperation, Any, str | None, str | None, str | None, int, int, int]] = []
            resolved_targets: set[Any] = set()
            for operation in operations:
                target = self.guard.resolve(operation.path)
                self.guard.resolve(target.parent)
                if target in resolved_targets:
                    raise PatchFormatError(f"patch contains duplicate target paths: {operation.path}")
                resolved_targets.add(target)
                original = None
                before_hash: str | None = None
                before_size = 0
                before_mtime = 0
                if target.exists():
                    if not target.is_file():
                        raise PatchFormatError(f"patch target is not a file: {operation.path}")
                    try:
                        stat = target.stat()
                        before_size = stat.st_size
                        before_mtime = stat.st_mtime_ns
                        if before_size > _MAX_TARGET_FILE_BYTES:
                            raise PatchFormatError(f"patch target exceeds the {_MAX_TARGET_FILE_BYTES}-byte limit: {operation.path}")
                        # Keep newline bytes intact so an edit does not turn a
                        # CRLF file into LF or add a trailing newline.
                        with target.open("r", encoding="utf-8", newline="") as stream:
                            original = stream.read()
                        before_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
                    except UnicodeDecodeError as exc:
                        raise PatchFormatError(f"patch target is not UTF-8 text: {operation.path}") from exc
                updated = _apply_operation(operation, original)
                planned.append((operation, target, original, updated, before_hash, before_size, before_mtime, len((updated or "").encode("utf-8"))))
        except (PatchFormatError, OSError, ValueError) as exc:
            return ToolResult(False, str(exc), {"error": "patch_invalid"})

        previews: list[str] = []
        import uuid
        transaction_id = uuid.uuid4().hex
        change_operations = tuple(
            ChangeOperation(
                path=operation.path,
                operation="delete" if operation.delete else ("create" if operation.create else "update"),
                before_sha256=before_hash,
                after_sha256=hashlib.sha256((updated or "").encode("utf-8")).hexdigest() if updated is not None else None,
                before_bytes=len((original or "").encode("utf-8")),
                after_bytes=after_bytes,
                newline="\r\n" if original is not None and "\r\n" in original else "\n",
            )
            for operation, _target, original, updated, before_hash, _before_size, _before_mtime, after_bytes in planned
        )
        for operation, target, original, updated, _before_hash, _before_size, _before_mtime, _after_bytes in planned:
            before = (original or "").splitlines(keepends=True)
            after = (updated or "").splitlines(keepends=True)
            previews.extend(difflib.unified_diff(before, after, fromfile=f"a/{operation.path}", tofile=f"b/{operation.path}"))
        preview = "".join(previews)
        preview_truncated = len(preview) > _MAX_PREVIEW_CHARS
        safe_preview = preview[:_MAX_PREVIEW_CHARS] + ("\n[patch preview truncated]" if preview_truncated else "")
        change_plan = ChangePlan(transaction_id, change_operations, safe_preview)
        approval_arguments = {"patch": safe_preview, "allow_delete": allow_delete, "transaction_id": transaction_id, "operations": [operation.path for operation in change_operations], "change_plan": asdict(change_plan)}
        if not context.request_approval(self.definition.name, approval_arguments):
            return ToolResult(False, "apply_patch denied by approval policy", {"error": "approval_denied", "approval": "denied", "diff": safe_preview, "transaction_id": transaction_id, "operations": [asdict(op) for op in change_operations], "change_plan": asdict(change_plan)})
        # The approval callback may request cancellation. Re-check before
        # transaction preparation and filesystem writes so a late provider or
        # CLI cancel cannot leak a side effect.
        if context.cancelled:
            return ToolResult(False, "apply_patch cancelled after approval", {"error": "cancelled", "approval": "approved", "cancellation_reason": context.cancellation_reason, "diff": safe_preview, "transaction_id": transaction_id, "operations": [asdict(op) for op in change_operations]})
        if context.remaining_seconds(45.0) <= 0:
            return ToolResult(False, "apply_patch skipped because the run deadline has expired", {"error": "deadline_exceeded", "transaction_id": transaction_id, "diff": safe_preview})
        stale = context.deny_if_stale(self.definition.name)
        if stale:
            return stale
        if context.cancelled:
            return ToolResult(False, "apply_patch cancelled before transaction preparation", {"error": "cancelled", "approval": "approved", "cancellation_reason": context.cancellation_reason, "diff": safe_preview, "transaction_id": transaction_id, "operations": [asdict(op) for op in change_operations]})
        # Optimistic concurrency: an approval callback may take time or even
        # modify a target. Never silently overwrite that newer content.
        for operation, target, _original, _updated, before_hash, before_size, before_mtime, _after_bytes in planned:
            current_hash = None
            current_size = 0
            current_mtime = 0
            if target.exists():
                stat = target.stat()
                current_size, current_mtime = stat.st_size, stat.st_mtime_ns
                if target.is_file():
                    current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if (current_hash, current_size, current_mtime) != (before_hash, before_size, before_mtime):
                return ToolResult(False, f"patch conflict: {operation.path} changed after preview", {"error": "concurrency_conflict", "transaction_id": transaction_id, "path": operation.path, "diff": safe_preview})
        transaction_manifest = None
        if context.transaction_store is not None:
            try:
                transaction_manifest = context.transaction_store.prepare(
                    transaction_id=transaction_id,
                    run_id=context.run_id,
                    tool=self.definition.name,
                    operations=[{key: value for key, value in asdict(operation).items() if key in {"path", "operation", "before_sha256", "after_sha256", "before_bytes", "after_bytes", "encoding", "newline"}} for operation in change_operations],
                    before_bytes={operation.path: (original.encode("utf-8") if original is not None else None) for operation, _target, original, _updated, _before_hash, _before_size, _before_mtime, _after_bytes in planned},
                    preview=safe_preview,
                    plan_id=context.plan_id,
                    plan_item_id=context.plan_item_id,
                )
            except Exception as exc:
                return ToolResult(False, f"transaction could not be prepared: {type(exc).__name__}", {"error": "transaction_prepare_failed", "transaction_id": transaction_id, "diff": safe_preview})
        if context.pause_wait is not None:
            try:
                context.pause_wait()
            except PauseRequested:
                if transaction_manifest is not None:
                    try:
                        context.transaction_store.fail(transaction_id, "paused before write", recovery_required=False)
                    except Exception:
                        pass
                return ToolResult(False, "apply_patch paused before write", {"error": "paused", "transaction_id": transaction_id, "diff": safe_preview, "operations": [asdict(op) for op in change_operations]})
        if context.cancelled:
            if transaction_manifest is not None:
                try:
                    context.transaction_store.fail(transaction_id, "cancelled before write", recovery_required=False)
                except Exception:
                    pass
            return ToolResult(False, "apply_patch cancelled before write", {"error": "cancelled", "approval": "approved", "cancellation_reason": context.cancellation_reason, "diff": safe_preview, "transaction_id": transaction_id, "operations": [asdict(op) for op in change_operations]})
        if context.remaining_seconds(45.0) <= 0:
            if transaction_manifest is not None:
                try:
                    context.transaction_store.fail(transaction_id, "deadline exceeded before write", recovery_required=False)
                except Exception:
                    pass
            return ToolResult(False, "apply_patch deadline expired before write", {"error": "deadline_exceeded", "transaction_id": transaction_id, "diff": safe_preview})
        written: list[tuple[Any, str | None]] = []
        try:
            for operation, target, _original, updated, _before_hash, _before_size, _before_mtime, _after_bytes in planned:
                written.append((target, _original))
                if context.pause_wait is not None:
                    context.pause_wait()
                if context.remaining_seconds(45.0) <= 0:
                    raise _PatchDeadlineExceeded()
                if updated is None:
                    target.unlink()
                else:
                    _atomic_write(target, updated)
        except _PatchDeadlineExceeded:
            for target, original in reversed(written):
                try:
                    if original is None:
                        if target.exists(): target.unlink()
                    else:
                        _atomic_write(target, original)
                except OSError:
                    pass
            if transaction_manifest is not None:
                try: context.transaction_store.fail(transaction_id, "deadline exceeded during atomic replacement", recovery_required=False)
                except Exception: pass
            return ToolResult(False, "apply_patch deadline expired during atomic replacement", {"error": "deadline_exceeded", "transaction_id": transaction_id, "diff": safe_preview, "rolled_back": True})
        except PauseRequested:
            for target, original in reversed(written):
                try:
                    if original is None:
                        if target.exists():
                            target.unlink()
                    else:
                        _atomic_write(target, original)
                except OSError:
                    pass
            if transaction_manifest is not None:
                try:
                    context.transaction_store.fail(transaction_id, "paused during atomic replacement", recovery_required=False)
                except Exception:
                    pass
            return ToolResult(False, "apply_patch paused during atomic replacement", {"error": "paused", "transaction_id": transaction_id, "diff": safe_preview, "rolled_back": True, "operations": [asdict(op) for op in change_operations]})
        except OSError as exc:
            for target, original in reversed(written):
                try:
                    if original is None:
                        if target.exists():
                            target.unlink()
                    else:
                        _atomic_write(target, original)
                except OSError:
                    pass
            if transaction_manifest is not None:
                try:
                    context.transaction_store.fail(transaction_id, str(exc), recovery_required=False)
                except Exception:
                    pass
            return ToolResult(False, f"patch write failed: {exc}", {"error": "write_failed", "diff": safe_preview, "transaction_id": transaction_id, "rolled_back": True, "operations": [asdict(op) for op in change_operations]})
        if transaction_manifest is not None:
            try:
                context.transaction_store.commit(transaction_id)
            except Exception as exc:
                rolled_back = True
                for target, original in reversed(written):
                    try:
                        if original is None:
                            if target.exists(): target.unlink()
                        else:
                            _atomic_write(target, original)
                    except OSError:
                        rolled_back = False
                try:
                    context.transaction_store.fail(transaction_id, f"{type(exc).__name__}: {exc}", recovery_required=not rolled_back)
                except Exception:
                    pass
                return ToolResult(False, f"transaction commit failed: {type(exc).__name__}", {"error": "transaction_commit_failed", "transaction_id": transaction_id, "diff": safe_preview, "operations": [asdict(op) for op in change_operations], "rolled_back": rolled_back})
        changed = [operation.path for operation, _target, _original, _updated, _before_hash, _before_size, _before_mtime, _after_bytes in planned]
        metadata = {
            "paths": changed,
            "hunks": sum(len(operation.hunks) for operation in operations),
            "created": [operation.path for operation in operations if operation.create],
            "deleted": [operation.path for operation in operations if operation.delete],
            "old_chars": sum(len(original or "") for _operation, _target, original, _updated, _before_hash, _before_size, _before_mtime, _after_bytes in planned),
            "new_chars": sum(len(updated or "") for _operation, _target, _original, updated, _before_hash, _before_size, _before_mtime, _after_bytes in planned),
            "approval": "approved",
            "mutated": True,
            "diff": safe_preview,
            "truncated": preview_truncated,
            "transaction_id": transaction_id,
            "transaction": "committed",
            "operations": [asdict(op) for op in change_operations],
            "change_plan": asdict(ChangePlan(transaction_id, change_operations, safe_preview, approval="approved")),
            "change_result": asdict(ChangeResult(transaction_id, True, change_operations)),
        }
        return ToolResult(True, f"patched {', '.join(changed)}", metadata)
