from .base import AgentMode, ToolContext, ToolDefinition, ToolRegistry, ToolResult
from .filesystem import ListFilesTool, ReadFileTool, SearchTool, WriteFileTool
from .patch import ApplyPatchTool, ChangeOperation, ChangePlan, ChangeResult, PatchFormatError, parse_patch
from .summary import WorkspaceSummaryTool
from .repository_map import RepositoryMapTool
from .shell import AllowAllApproval, DenyAllApproval, InteractiveApproval, ShellTool
from ..hooks import Hook, HookIssue, HookRegistry


def build_default_registry(guard) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListFilesTool(guard))
    registry.register(ReadFileTool(guard))
    registry.register(SearchTool(guard))
    registry.register(WriteFileTool(guard))
    registry.register(ApplyPatchTool(guard))
    registry.register(WorkspaceSummaryTool(guard))
    registry.register(RepositoryMapTool(guard))
    registry.register(ShellTool(guard))
    return registry


__all__ = [
    "AllowAllApproval",
    "ApplyPatchTool",
    "ChangeOperation",
    "ChangePlan",
    "ChangeResult",
    "PatchFormatError",
    "parse_patch",
    "WorkspaceSummaryTool",
    "RepositoryMapTool",
    "AgentMode",
    "DenyAllApproval",
    "InteractiveApproval",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "Hook",
    "HookIssue",
    "HookRegistry",
    "build_default_registry",
]
