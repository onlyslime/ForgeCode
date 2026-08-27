from .base import AgentMode, ToolContext, ToolDefinition, ToolRegistry, ToolResult
from .filesystem import ListFilesTool, ReadFileTool, SearchTool, WriteFileTool
from .patch import ApplyPatchTool
from .summary import WorkspaceSummaryTool
from .shell import AllowAllApproval, DenyAllApproval, InteractiveApproval, ShellTool


def build_default_registry(guard) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListFilesTool(guard))
    registry.register(ReadFileTool(guard))
    registry.register(SearchTool(guard))
    registry.register(WriteFileTool(guard))
    registry.register(ApplyPatchTool(guard))
    registry.register(WorkspaceSummaryTool(guard))
    registry.register(ShellTool(guard))
    return registry


__all__ = [
    "AllowAllApproval",
    "ApplyPatchTool",
    "WorkspaceSummaryTool",
    "AgentMode",
    "DenyAllApproval",
    "InteractiveApproval",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
]
