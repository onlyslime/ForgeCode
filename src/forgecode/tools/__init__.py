from .base import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from .filesystem import ListFilesTool, ReadFileTool, SearchTool, WriteFileTool
from .shell import AllowAllApproval, DenyAllApproval, InteractiveApproval, ShellTool


def build_default_registry(guard) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListFilesTool(guard))
    registry.register(ReadFileTool(guard))
    registry.register(SearchTool(guard))
    registry.register(WriteFileTool(guard))
    registry.register(ShellTool(guard))
    return registry


__all__ = [
    "AllowAllApproval",
    "DenyAllApproval",
    "InteractiveApproval",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
]
