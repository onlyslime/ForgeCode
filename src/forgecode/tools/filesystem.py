"""Workspace-scoped filesystem and search tools."""

import re
from typing import Any

from .base import ToolContext, ToolDefinition, ToolResult


_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}


def _required(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


class ListFilesTool:
    definition = ToolDefinition(
        "list_files",
        "List workspace files matching a glob.",
        {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        pattern = _required(arguments, "pattern")
        matches = []
        for path in context.guard.root.glob(pattern):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.is_file():
                matches.append(context.guard.relative(path))
        return ToolResult(True, "\n".join(sorted(matches)), {"count": len(matches)})


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
        max_chars = int(arguments.get("max_chars", 20_000))
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        content = path.read_text(encoding="utf-8")
        return ToolResult(True, content[:max_chars], {"path": context.guard.relative(path), "truncated": len(content) > max_chars})


class SearchTool:
    definition = ToolDefinition(
        "search",
        "Search text or a regular expression in workspace files.",
        {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "max_matches": {"type": "integer", "minimum": 1}}, "required": ["query"]},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        query = _required(arguments, "query")
        root = context.guard.resolve(arguments.get("path", "."))
        pattern = re.compile(query)
        limit = int(arguments.get("max_matches", 100))
        if limit < 1:
            raise ValueError("max_matches must be positive")
        results = []
        files = [root] if root.is_file() else root.rglob("*")
        for path in files:
            if len(results) >= limit:
                break
            if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    results.append(f"{context.guard.relative(path)}:{number}:{line}")
                    if len(results) >= limit:
                        break
        return ToolResult(True, "\n".join(results), {"count": len(results)})


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
        if context.approval is None or not context.approval.approve(self.definition.name, arguments):
            return ToolResult(False, "write_file denied by approval policy", {"error": "approval_denied"})
        path = context.guard.resolve(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(True, f"wrote {context.guard.relative(path)}", {"path": context.guard.relative(path), "bytes": len(content.encode("utf-8"))})
