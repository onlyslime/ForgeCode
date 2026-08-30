"""Workspace-scoped filesystem and search tools."""

import os
import re
import tempfile
import hashlib
import uuid
import fnmatch
from pathlib import Path
from typing import Any

from ..context_policy import is_ignored_context_path
from .base import PauseRequested, ToolContext, ToolDefinition, ToolResult


_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".forgecode", "dist", "build", "tmp", "temp"}
_SKIP_FILES = {".env", ".env.local", ".env.example", "id_rsa", "credentials.json"}
_MAX_LIST_FILES = 1_000
_MAX_FILE_BYTES = 2_000_000
_MAX_READ_CHARS = 100_000
_MAX_SEARCH_MATCHES = 1_000
_MAX_SEARCH_LINE_CHARS = 4_000
_MAX_WRITE_CHARS = 1_000_000


def _is_ignored(path, guard) -> bool:
    return is_ignored_context_path(guard, path)


def _is_skipped(path, guard) -> bool:
    return _is_ignored(path, guard)


def _required(arguments: dict[str, Any], name: str) -> str:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _positive_int(arguments: dict[str, Any], name: str, default: int, maximum: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


class ListFilesTool:
    definition = ToolDefinition(
        "list_files",
        "List workspace files matching a glob while excluding generated and sensitive paths.",
        {"type": "object", "properties": {"pattern": {"type": "string"}, "max_files": {"type": "integer", "minimum": 1, "maximum": _MAX_LIST_FILES}}, "required": ["pattern"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        pattern = _required(arguments, "pattern")
        normalized_pattern = pattern.replace("\\", "/")
        if pattern.startswith(("/", "\\")) or (len(pattern) >= 2 and pattern[1] == ":") or any(part == ".." for part in normalized_pattern.split("/")):
            raise ValueError("pattern must stay inside the workspace")
        limit = _positive_int(arguments, "max_files", _MAX_LIST_FILES, _MAX_LIST_FILES)
        candidates = []
        # Walk incrementally instead of letting Path.glob materialise an
        # unbounded recursive result supplied by an untrusted model.
        visited = 0
        for directory, names, filenames in os.walk(context.guard.root, topdown=True, followlinks=False):
            directory_path = Path(directory)
            names[:] = [name for name in sorted(names, key=str.lower) if not _is_skipped(directory_path / name, context.guard)]
            for name in sorted(filenames, key=str.lower):
                path = directory_path / name
                visited += 1
                if visited > _MAX_LIST_FILES * 20:
                    break
                try:
                    if not fnmatch.fnmatch(path.relative_to(context.guard.root).as_posix(), normalized_pattern):
                        continue
                except (ValueError, OSError):
                    continue
                if _is_skipped(path, context.guard):
                    continue
                try:
                    safe_path = context.guard.resolve(path, must_exist=True)
                    if safe_path.is_file() and safe_path == path.absolute():
                        candidates.append(context.guard.relative(safe_path))
                except (OSError, ValueError):
                    continue
            if visited > _MAX_LIST_FILES * 20:
                break
        matches = sorted(set(candidates))
        total_count = len(matches)
        truncated = len(matches) > limit
        matches = matches[:limit]
        omitted = max(0, total_count - len(matches))
        output = "\n".join(matches)
        if omitted:
            output += f"\n[{omitted} files omitted]"
        return ToolResult(True, output, {"count": len(matches), "total_count": total_count, "omitted": omitted, "limit": limit, "truncated": truncated})


class ReadFileTool:
    definition = ToolDefinition(
        "read_file",
        "Read a UTF-8 text file inside the workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 1}}, "required": ["path"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        path_value = _required(arguments, "path")
        path = context.guard.resolve(path_value, must_exist=True)
        lexical = Path(path_value)
        if not lexical.is_absolute():
            lexical = context.guard.root / lexical
        if path != lexical.absolute():
            raise ValueError("reading symlink or junction aliases is not allowed")
        if _is_ignored(path, context.guard):
            raise ValueError("reading ignored or sensitive files is not allowed")
        if not path.is_file():
            raise ValueError(f"not a file: {arguments['path']}")
        max_chars = _positive_int(arguments, "max_chars", 20_000, _MAX_READ_CHARS)
        try:
            before_stat = path.stat()
            if before_stat.st_size > _MAX_FILE_BYTES:
                raise ValueError(f"file exceeds the {_MAX_FILE_BYTES}-byte safety limit")
            raw = path.read_bytes()
            after_stat = path.stat()
            if (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0)) != (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0)):
                raise ValueError("file changed while it was read")
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"file is not valid UTF-8 text: {arguments['path']}") from exc
        except OSError as exc:
            raise ValueError(f"could not read file {arguments['path']}: {exc}") from exc
        return ToolResult(True, content[:max_chars], {"path": context.guard.relative(path), "truncated": len(content) > max_chars})


class SearchTool:
    definition = ToolDefinition(
        "search",
        "Search text or a regular expression in workspace files.",
        {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "regex": {"type": "boolean"}, "max_matches": {"type": "integer", "minimum": 1, "maximum": _MAX_SEARCH_MATCHES}}, "required": ["query"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        query = _required(arguments, "query")
        path_value = arguments.get("path", ".")
        root = context.guard.resolve(path_value, must_exist=True)
        lexical_root = Path(path_value)
        if not lexical_root.is_absolute():
            lexical_root = context.guard.root / lexical_root
        if root != lexical_root.absolute():
            raise ValueError("searching symlink or junction aliases is not allowed")
        use_regex = arguments.get("regex", False)
        if not isinstance(use_regex, bool):
            raise ValueError("regex must be a boolean")
        try:
            pattern = re.compile(query if use_regex else re.escape(query))
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        limit = _positive_int(arguments, "max_matches", 100, _MAX_SEARCH_MATCHES)
        results = []
        omitted = 0
        files = [root] if root.is_file() else root.rglob("*")
        for path in sorted(files, key=lambda candidate: candidate.as_posix()):
            if _is_skipped(path, context.guard):
                continue
            try:
                safe_path = context.guard.resolve(path)
                if not safe_path.is_file() or safe_path != path.absolute():
                    continue
                before_stat = safe_path.stat()
                if before_stat.st_size > _MAX_FILE_BYTES:
                    continue
                lines = safe_path.read_bytes().decode("utf-8").splitlines()
                after_stat = safe_path.stat()
                if (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0)) != (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0)):
                    continue
            except (UnicodeDecodeError, OSError, ValueError):
                continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    if len(results) < limit:
                        shown_line = line[:_MAX_SEARCH_LINE_CHARS] + ("..." if len(line) > _MAX_SEARCH_LINE_CHARS else "")
                        results.append(f"{context.guard.relative(safe_path)}:{number}:{shown_line}")
                    else:
                        omitted += 1
                    if omitted >= 1_000:
                        break
            if omitted >= 1_000:
                break
        output = "\n".join(results)
        if omitted:
            output += f"\n[at least {omitted} matches omitted]"
        return ToolResult(True, output, {"count": len(results), "omitted_at_least": omitted, "limit": limit, "truncated": omitted > 0})


class WriteFileTool:
    definition = ToolDefinition(
        "write_file",
        "Write UTF-8 text to a workspace file after approval.",
        {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        side_effecting=True,
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        path_value = _required(arguments, "path")
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content) > _MAX_WRITE_CHARS:
            raise ValueError(f"content exceeds the {_MAX_WRITE_CHARS}-character safety limit")
        path = context.guard.resolve(path_value)
        context.guard.resolve(path.parent)
        transaction_id = uuid.uuid4().hex
        before_exists = path.exists()
        before_hash = None
        before_content: bytes | None = None
        before_size = 0
        before_mtime = 0
        before_mode = None
        if before_exists:
            if not path.is_file():
                raise ValueError(f"not a file: {path_value}")
            stat = path.stat()
            before_size, before_mtime = stat.st_size, stat.st_mtime_ns
            before_mode = stat.st_mode
            before_content = path.read_bytes()
            before_hash = hashlib.sha256(before_content).hexdigest()
        preview = content[:4_000] + ("\n[content truncated]" if len(content) > 4_000 else "")
        approval_arguments = {"path": path_value, "content": preview, "transaction_id": transaction_id, "operation": "update" if before_exists else "create"}
        if not context.request_approval(self.definition.name, approval_arguments):
            return ToolResult(False, "write_file denied by approval policy", {"error": "approval_denied", "approval": "denied", "transaction_id": transaction_id})
        # Approval callbacks are untrusted and may race with cancellation.
        # Never prepare a transaction or touch the target after cancellation.
        if context.cancelled:
            return ToolResult(False, "write_file cancelled after approval", {"error": "cancelled", "approval": "approved", "cancellation_reason": context.cancellation_reason, "transaction_id": transaction_id, "path": path_value})
        stale = context.deny_if_stale(self.definition.name)
        if stale:
            return stale
        if context.cancelled:
            return ToolResult(False, "write_file cancelled before transaction preparation", {"error": "cancelled", "approval": "approved", "cancellation_reason": context.cancellation_reason, "transaction_id": transaction_id, "path": path_value})
        current_exists = path.exists()
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest() if current_exists and path.is_file() else None
        current_stat = path.stat() if current_exists else None
        if (current_hash, current_stat.st_size if current_stat else 0, current_stat.st_mtime_ns if current_stat else 0) != (before_hash, before_size, before_mtime):
            return ToolResult(False, f"write conflict: {path_value} changed after preview", {"error": "concurrency_conflict", "transaction_id": transaction_id, "path": path_value})
        transaction_manifest = None
        if context.transaction_store is not None:
            try:
                relative_path = context.guard.relative(path)
                after_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                transaction_manifest = context.transaction_store.prepare(
                    transaction_id=transaction_id,
                    run_id=context.run_id,
                    tool=self.definition.name,
                    operations=[{"path": relative_path, "operation": "update" if before_exists else "create", "before_sha256": before_hash, "after_sha256": after_hash, "before_bytes": before_size, "after_bytes": len(content.encode("utf-8")), "mode": before_mode}],
                    before_bytes={relative_path: before_content},
                    preview=preview,
                    plan_id=context.plan_id,
                    plan_item_id=context.plan_item_id,
                )
            except Exception as exc:
                return ToolResult(False, f"transaction could not be prepared: {type(exc).__name__}", {"error": "transaction_prepare_failed", "transaction_id": transaction_id})
        if context.pause_wait is not None:
            try:
                context.pause_wait()
            except PauseRequested:
                if transaction_manifest is not None:
                    try:
                        context.transaction_store.fail(transaction_id, "paused before write", recovery_required=False)
                    except Exception:
                        pass
                return ToolResult(False, "write_file paused before write", {"error": "paused", "transaction_id": transaction_id, "path": path_value})
        if context.cancelled:
            if transaction_manifest is not None:
                try:
                    context.transaction_store.fail(transaction_id, "cancelled before write", recovery_required=False)
                except Exception:
                    pass
            return ToolResult(False, "write_file cancelled before write", {"error": "cancelled", "approval": "approved", "cancellation_reason": context.cancellation_reason, "transaction_id": transaction_id, "path": path_value})
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".forgecode.tmp", dir=path.parent)
            temporary = context.guard.resolve(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                if before_mode is not None:
                    os.chmod(temporary, before_mode)
                if context.pause_wait is not None:
                    context.pause_wait()
                os.replace(temporary, path)
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                if temporary.exists():
                    temporary.unlink()
        except PauseRequested:
            if transaction_manifest is not None:
                try:
                    context.transaction_store.fail(transaction_id, "paused before atomic replace", recovery_required=False)
                except Exception:
                    pass
            return ToolResult(False, "write_file paused before atomic replace", {"error": "paused", "transaction_id": transaction_id, "path": path_value})
        except OSError as exc:
            rolled_back = False
            try:
                if before_content is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.write_bytes(before_content)
                rolled_back = True
            except OSError:
                pass
            if transaction_manifest is not None:
                try:
                    context.transaction_store.fail(transaction_id, str(exc), recovery_required=not rolled_back)
                except Exception:
                    pass
            return ToolResult(False, f"write failed: {exc}", {"error": "write_failed", "transaction_id": transaction_id, "rolled_back": rolled_back, "path": path_value})
        after_bytes = content.encode("utf-8")
        operation = "update" if before_exists else "create"
        if transaction_manifest is not None:
            try:
                context.transaction_store.commit(transaction_id)
            except Exception as exc:
                rolled_back = False
                try:
                    if before_content is None:
                        if path.exists():
                            path.unlink()
                    else:
                        path.write_bytes(before_content)
                    rolled_back = True
                except OSError:
                    pass
                try:
                    context.transaction_store.fail(transaction_id, f"{type(exc).__name__}: {exc}", recovery_required=not rolled_back)
                except Exception:
                    pass
                return ToolResult(False, f"transaction commit failed: {type(exc).__name__}", {"error": "transaction_commit_failed", "transaction_id": transaction_id, "path": path_value, "rolled_back": rolled_back})
        return ToolResult(True, f"wrote {context.guard.relative(path)}", {"path": context.guard.relative(path), "bytes": len(after_bytes), "approval": "approved", "mutated": True, "transaction_id": transaction_id, "transaction": "committed", "operation": operation, "before_sha256": before_hash, "after_sha256": hashlib.sha256(after_bytes).hexdigest(), "before_bytes": before_size, "after_bytes": len(after_bytes), "newline": "\r\n" if "\r\n" in content else "\n"})
