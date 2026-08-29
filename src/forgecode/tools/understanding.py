"""Read-only code understanding and file metadata tools."""
from __future__ import annotations
import re
from typing import Any
from .base import ToolContext, ToolDefinition, ToolResult
from .filesystem import _is_ignored, _positive_int, _required

class ReadRangeTool:
    definition = ToolDefinition("read_range", "Read a bounded line range from a UTF-8 workspace file.", {"type":"object","properties":{"path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["path","start_line","end_line"]})
    def __init__(self, guard): self.guard = guard
    def execute(self, arguments, context):
        path_value = _required(arguments, "path")
        start = arguments.get("start_line"); end = arguments.get("end_line")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or start < 1 or end < start or end - start > 500:
            raise ValueError("line range must be valid and at most 500 lines")
        path = context.guard.resolve(path_value, must_exist=True)
        if _is_ignored(path, context.guard) or not path.is_file(): raise ValueError("path is not a readable file")
        lines = path.read_text(encoding="utf-8").splitlines()
        shown = lines[start - 1:end]
        return ToolResult(True, "\n".join(f"{i} | {line}" for i, line in enumerate(shown, start)), {"path": context.guard.relative(path), "start_line": start, "end_line": min(end, len(lines)), "total_lines": len(lines)})

class ListSymbolsTool:
    definition = ToolDefinition("list_symbols", "List common function, class, method, and export symbols in a source file.", {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]})
    def __init__(self, guard): self.guard = guard
    def execute(self, arguments, context):
        path_value = _required(arguments, "path"); path = context.guard.resolve(path_value, must_exist=True)
        if _is_ignored(path, context.guard) or not path.is_file(): raise ValueError("path is not a readable file")
        text = path.read_text(encoding="utf-8")
        patterns = [r"^\s*(?:async\s+)?def\s+(\w+)", r"^\s*class\s+(\w+)", r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", r"^\s*(?:export\s+)?class\s+(\w+)", r"^\s*export\s+(?:const|let|var)\s+(\w+)"]
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    rows.append(f"{number}: {match.group(1)}")
                    break
        return ToolResult(True, "\n".join(rows[:500]) or "no symbols detected", {"path": context.guard.relative(path), "count": len(rows)})

class FileMetadataTool:
    definition = ToolDefinition("file_metadata", "Show bounded metadata including size, lines, encoding, modified time, and SHA-256.", {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]})
    def __init__(self, guard): self.guard = guard
    def execute(self, arguments, context):
        path_value = _required(arguments, "path"); path = context.guard.resolve(path_value, must_exist=True)
        if _is_ignored(path, context.guard) or not path.is_file(): raise ValueError("path is not a readable file")
        raw = path.read_bytes()
        import hashlib
        try: text = raw.decode("utf-8"); encoding = "utf-8"
        except UnicodeDecodeError: text = ""; encoding = "binary"
        stat = path.stat()
        data = {"path": context.guard.relative(path), "size_bytes": len(raw), "lines": len(text.splitlines()) if text else None, "encoding": encoding, "modified_ns": stat.st_mtime_ns, "sha256": hashlib.sha256(raw).hexdigest()}
        return ToolResult(True, "\n".join(f"{key}: {value}" for key, value in data.items()), data)
