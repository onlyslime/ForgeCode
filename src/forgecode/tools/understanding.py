"""Read-only code understanding and file metadata tools."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from .base import ToolContext, ToolDefinition, ToolResult
from .filesystem import _is_ignored, _positive_int, _required, _MAX_FILE_BYTES

class ReadRangeTool:
    definition = ToolDefinition("read_range", "Read a bounded line range from a UTF-8 workspace file.", {"type":"object","properties":{"path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["path","start_line","end_line"]})
    def __init__(self, guard): self.guard = guard
    def execute(self, arguments, context):
        if context.cancelled:
            return ToolResult(False, "range read cancelled before access", {"error": "cancelled"})
        if context.remaining_seconds(10.0) <= 0:
            return ToolResult(False, "range read skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        path_value = _required(arguments, "path")
        start = arguments.get("start_line"); end = arguments.get("end_line")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or start < 1 or end < start or end - start > 500:
            raise ValueError("line range must be valid and at most 500 lines")
        path = context.guard.resolve(path_value, must_exist=True)
        if _is_ignored(path, context.guard) or not path.is_file(): raise ValueError("path is not a readable file")
        if path.stat().st_size > _MAX_FILE_BYTES:
            return ToolResult(False, f"file exceeds the {_MAX_FILE_BYTES}-byte safety limit", {"error": "file_too_large", "path": path_value})
        lines = path.read_text(encoding="utf-8").splitlines()
        shown = lines[start - 1:end]
        return ToolResult(True, "\n".join(f"{i} | {line}" for i, line in enumerate(shown, start)), {"path": context.guard.relative(path), "start_line": start, "end_line": min(end, len(lines)), "total_lines": len(lines)})

class ListSymbolsTool:
    definition = ToolDefinition("list_symbols", "List common function, class, method, and export symbols in a source file.", {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]})
    def __init__(self, guard): self.guard = guard
    def execute(self, arguments, context):
        if context.cancelled:
            return ToolResult(False, "symbol listing cancelled before access", {"error": "cancelled"})
        if context.remaining_seconds(10.0) <= 0:
            return ToolResult(False, "symbol listing skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        path_value = _required(arguments, "path"); path = context.guard.resolve(path_value, must_exist=True)
        if _is_ignored(path, context.guard) or not path.is_file(): raise ValueError("path is not a readable file")
        if path.stat().st_size > _MAX_FILE_BYTES:
            return ToolResult(False, f"file exceeds the {_MAX_FILE_BYTES}-byte safety limit", {"error": "file_too_large", "path": path_value})
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


def _source_files(context: ToolContext, path_value: str | None = None):
    """Yield bounded, guarded text files without importing or executing code."""
    if path_value is not None and not isinstance(path_value, str):
        raise ValueError("path must be a string")
    if path_value:
        candidate = context.guard.resolve(path_value, must_exist=True)
        lexical = Path(path_value)
        if not lexical.is_absolute():
            lexical = context.guard.root / lexical
        if candidate == lexical.absolute() and candidate.is_file() and not _is_ignored(candidate, context.guard):
            yield candidate
        return
    count = 0
    for candidate in context.guard.root.rglob("*"):
        if count >= 500:
            break
        if not candidate.is_file() or _is_ignored(candidate, context.guard):
            continue
        try:
            safe = context.guard.resolve(candidate, must_exist=True)
        except (OSError, ValueError):
            continue
        if safe != candidate.absolute():
            continue
        if candidate.suffix.lower() in {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cs"}:
            count += 1
            yield candidate


class FindDefinitionTool:
    definition = ToolDefinition("find_definition", "Find bounded source definitions for a symbol without executing project code.", {"type":"object","properties":{"symbol":{"type":"string"},"path":{"type":"string"}},"required":["symbol"]})
    def execute(self, arguments, context):
        if context.cancelled:
            return ToolResult(False, "definition search cancelled before scan", {"error": "cancelled"})
        if context.remaining_seconds(20.0) <= 0:
            return ToolResult(False, "definition search skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        symbol = _required(arguments, "symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z_]\w{0,127}", symbol):
            raise ValueError("symbol must be a simple identifier")
        pattern = re.compile(rf"^\s*(?:(?:async)\s+)?(?:def|class|function|export\s+(?:async\s+)?function|export\s+class)\s+{re.escape(symbol)}\b|^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(symbol)}\s*=.*=>")
        rows = []
        for path in _source_files(context, arguments.get("path")):
            if context.cancelled:
                return ToolResult(False, "definition search cancelled during scan", {"error": "cancelled"})
            if context.remaining_seconds(20.0) <= 0:
                return ToolResult(False, "definition search stopped because the run deadline has expired", {"error": "deadline_exceeded"})
            try: lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError): continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line): rows.append({"path": context.guard.relative(path), "line": number, "symbol": symbol, "text": line.strip()[:300]})
                if len(rows) >= 200: break
            if len(rows) >= 200: break
        return ToolResult(True, "\n".join(f"{item['path']}:{item['line']} {item['text']}" for item in rows) or "definition not found", {"symbol": symbol, "matches": rows, "count": len(rows)})


class FindReferencesTool:
    definition = ToolDefinition("find_references", "Find bounded textual references to a symbol in source files.", {"type":"object","properties":{"symbol":{"type":"string"},"path":{"type":"string"}},"required":["symbol"]})
    def execute(self, arguments, context):
        if context.cancelled:
            return ToolResult(False, "reference search cancelled before scan", {"error": "cancelled"})
        if context.remaining_seconds(20.0) <= 0:
            return ToolResult(False, "reference search skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        symbol = _required(arguments, "symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z_]\w{0,127}", symbol):
            raise ValueError("symbol must be a simple identifier")
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        rows = []
        for path in _source_files(context, arguments.get("path")):
            if context.cancelled:
                return ToolResult(False, "reference search cancelled during scan", {"error": "cancelled"})
            if context.remaining_seconds(20.0) <= 0:
                return ToolResult(False, "reference search stopped because the run deadline has expired", {"error": "deadline_exceeded"})
            try: lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError): continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line): rows.append({"path": context.guard.relative(path), "line": number, "text": line.strip()[:300]})
                if len(rows) >= 500: break
            if len(rows) >= 500: break
        return ToolResult(True, "\n".join(f"{item['path']}:{item['line']} {item['text']}" for item in rows) or "no references found", {"symbol": symbol, "matches": rows, "count": len(rows)})


class SymbolHoverTool:
    definition = ToolDefinition("symbol_hover", "Show a bounded static definition and nearby source context for a symbol.", {"type":"object","properties":{"symbol":{"type":"string"},"path":{"type":"string"},"context_lines":{"type":"integer"}},"required":["symbol"]})
    def execute(self, arguments, context):
        if context.cancelled:
            return ToolResult(False, "symbol hover cancelled before scan", {"error": "cancelled"})
        if context.remaining_seconds(20.0) <= 0:
            return ToolResult(False, "symbol hover skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        symbol = _required(arguments, "symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z_]\w{0,127}", symbol): raise ValueError("symbol must be a simple identifier")
        radius = arguments.get("context_lines", 2)
        if isinstance(radius, bool) or not isinstance(radius, int) or not 0 <= radius <= 10: raise ValueError("context_lines must be between 0 and 10")
        pattern = re.compile(rf"^\s*(?:(?:async)\s+)?(?:def|class|function|export\s+(?:async\s+)?function|export\s+class)\s+{re.escape(symbol)}\b|^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(symbol)}\s*=.*=>")
        for path in _source_files(context, arguments.get("path")):
            if context.cancelled:
                return ToolResult(False, "symbol hover cancelled during scan", {"error": "cancelled"})
            if context.remaining_seconds(20.0) <= 0:
                return ToolResult(False, "symbol hover stopped because the run deadline has expired", {"error": "deadline_exceeded"})
            try: lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError): continue
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    start, end = max(1, number-radius), min(len(lines), number+radius)
                    snippet = "\n".join(f"{i} | {lines[i-1]}" for i in range(start, end+1))
                    data = {"symbol": symbol, "path": context.guard.relative(path), "line": number, "context_lines": radius, "snippet": snippet, "precision": "static"}
                    return ToolResult(True, snippet, data)
        return ToolResult(True, "definition not found", {"symbol": symbol, "matches": [], "precision": "static"})

class FileMetadataTool:
    definition = ToolDefinition("file_metadata", "Show bounded metadata including size, lines, encoding, modified time, and SHA-256.", {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]})
    def __init__(self, guard): self.guard = guard
    def execute(self, arguments, context):
        if context.cancelled:
            return ToolResult(False, "file metadata cancelled before access", {"error": "cancelled"})
        if context.remaining_seconds(10.0) <= 0:
            return ToolResult(False, "file metadata skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        path_value = _required(arguments, "path"); path = context.guard.resolve(path_value, must_exist=True)
        lexical = Path(path_value)
        if not lexical.is_absolute():
            lexical = context.guard.root / lexical
        if path != lexical.absolute():
            raise ValueError("reading symlink or junction aliases is not allowed")
        if _is_ignored(path, context.guard) or not path.is_file(): raise ValueError("path is not a readable file")
        if path.stat().st_size > _MAX_FILE_BYTES:
            return ToolResult(False, f"file exceeds the {_MAX_FILE_BYTES}-byte safety limit", {"error": "file_too_large", "path": path_value})
        raw = path.read_bytes()
        if len(raw) > _MAX_FILE_BYTES:
            return ToolResult(False, f"file exceeds the {_MAX_FILE_BYTES}-byte safety limit", {"error": "file_too_large", "path": path_value})
        import hashlib
        try: text = raw.decode("utf-8"); encoding = "utf-8"
        except UnicodeDecodeError: text = ""; encoding = "binary"
        stat = path.stat()
        data = {"path": context.guard.relative(path), "size_bytes": len(raw), "lines": len(text.splitlines()) if text else None, "encoding": encoding, "modified_ns": stat.st_mtime_ns, "sha256": hashlib.sha256(raw).hexdigest()}
        return ToolResult(True, "\n".join(f"{key}: {value}" for key, value in data.items()), data)
