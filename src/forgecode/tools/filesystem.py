"""Workspace-scoped filesystem and search tools."""

import os
import re
import tempfile
from typing import Any

from .base import ToolContext, ToolDefinition, ToolResult


_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
_MAX_LIST_FILES = 1_000
_MAX_FILE_BYTES = 2_000_000
_MAX_READ_CHARS = 100_000
_MAX_SEARCH_MATCHES = 1_000
_MAX_SEARCH_LINE_CHARS = 4_000
_MAX_WRITE_CHARS = 1_000_000


def _is_skipped(path, guard) -> bool:
    try:
        relative_parts = guard.relative(path).split("/")
    except (OSError, ValueError):
        return True
    return any(part in _SKIP_DIRS for part in relative_parts)


def _required(arguments: dict[str, Any], name: str) -> str:
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
        "List workspace files matching a glob.",
        {"type": "object", "properties": {"pattern": {"type": "string"}, "max_files": {"type": "integer", "minimum": 1, "maximum": _MAX_LIST_FILES}}, "required": ["pattern"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        pattern = _required(arguments, "pattern")
        limit = _positive_int(arguments, "max_files", _MAX_LIST_FILES, _MAX_LIST_FILES)
        candidates = []
        for path in context.guard.root.glob(pattern):
            if _is_skipped(path, context.guard):
                continue
            try:
                safe_path = context.guard.resolve(path)
                if safe_path.is_file() and safe_path == path.resolve():
                    candidates.append(context.guard.relative(safe_path))
            except (OSError, ValueError):
                continue
        matches = sorted(set(candidates))
        truncated = len(matches) > limit
        matches = matches[:limit]
        return ToolResult(True, "\n".join(matches), {"count": len(matches), "limit": limit, "truncated": truncated})


class ReadFileTool:
    definition = ToolDefinition(
        "read_file",
        "Read a UTF-8 text file inside the workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 1}}, "required": ["path"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        path = context.guard.resolve(_required(arguments, "path"), must_exist=True)
        if not path.is_file():
            raise ValueError(f"not a file: {arguments['path']}")
        max_chars = _positive_int(arguments, "max_chars", 20_000, _MAX_READ_CHARS)
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                raise ValueError(f"file exceeds the {_MAX_FILE_BYTES}-byte safety limit")
            content = path.read_text(encoding="utf-8")
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
        query = _required(arguments, "query")
        root = context.guard.resolve(arguments.get("path", "."), must_exist=True)
        use_regex = arguments.get("regex", False)
        if not isinstance(use_regex, bool):
            raise ValueError("regex must be a boolean")
        try:
            pattern = re.compile(query if use_regex else re.escape(query))
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        limit = _positive_int(arguments, "max_matches", 100, _MAX_SEARCH_MATCHES)
        results = []
        files = [root] if root.is_file() else root.rglob("*")
        for path in sorted(files, key=lambda candidate: candidate.as_posix()):
            if len(results) >= limit:
                break
            if _is_skipped(path, context.guard):
                continue
            try:
                safe_path = context.guard.resolve(path)
                if not safe_path.is_file() or safe_path != path.resolve():
                    continue
                if safe_path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                lines = safe_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError, ValueError):
                continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    shown_line = line[:_MAX_SEARCH_LINE_CHARS] + ("..." if len(line) > _MAX_SEARCH_LINE_CHARS else "")
                    results.append(f"{context.guard.relative(safe_path)}:{number}:{shown_line}")
                    if len(results) >= limit:
                        break
        return ToolResult(True, "\n".join(results), {"count": len(results), "limit": limit, "truncated": len(results) >= limit})


class WriteFileTool:
    definition = ToolDefinition(
        "write_file",
        "Write UTF-8 text to a workspace file after approval.",
        {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        path_value = _required(arguments, "path")
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content) > _MAX_WRITE_CHARS:
            raise ValueError(f"content exceeds the {_MAX_WRITE_CHARS}-character safety limit")
        path = context.guard.resolve(path_value)
        context.guard.resolve(path.parent)
        if not context.request_approval(self.definition.name, arguments):
            return ToolResult(False, "write_file denied by approval policy", {"error": "approval_denied", "approval": "denied"})
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".forgecode.tmp", dir=path.parent)
        temporary = context.guard.resolve(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            if temporary.exists():
                temporary.unlink()
        return ToolResult(True, f"wrote {context.guard.relative(path)}", {"path": context.guard.relative(path), "bytes": len(content.encode("utf-8")), "approval": "approved", "mutated": True})
