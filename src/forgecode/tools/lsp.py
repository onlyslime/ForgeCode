"""Safe discovery of locally installed language-server executables."""
from __future__ import annotations
import shutil
from .base import ToolDefinition, ToolResult


class LspStatusTool:
    definition = ToolDefinition(
        "lsp_status",
        "Report common language-server executables available on PATH without starting them.",
        {"type": "object", "additionalProperties": False},
    )

    _SERVERS = {
        "python": ("pyright-langserver", "pylsp", "basedpyright-langserver"),
        "typescript": ("typescript-language-server",),
        "rust": ("rust-analyzer",),
        "go": ("gopls",),
        "java": ("jdtls",),
        "c_cpp": ("clangd",),
    }

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        rows = []
        for language, candidates in self._SERVERS.items():
            found = next((name for name in candidates if shutil.which(name)), None)
            rows.append({"language": language, "available": found is not None, "executable": found})
        available = [row for row in rows if row["available"]]
        data = {"supported": False, "mode": "discovery_only", "servers": rows, "available_count": len(available)}
        text = "\n".join(f"{row['language']}: {row['executable'] or 'not found'}" for row in rows)
        return ToolResult(True, text, data)
