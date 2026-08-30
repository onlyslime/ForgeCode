from .base import AgentMode, PauseRequested, ToolContext, ToolDefinition, ToolRegistry, ToolResult
from .filesystem import ListFilesTool, ReadFileTool, SearchTool, WriteFileTool
from .patch import ApplyPatchTool, ChangeOperation, ChangePlan, ChangeResult, PatchFormatError, parse_patch
from .summary import WorkspaceSummaryTool
from .repository_map import RepositoryMapTool
from .shell import AllowAllApproval, DenyAllApproval, InteractiveApproval, RiskScopedApproval, ShellTool
from .git import GitCommitTool, GitDiffTool, GitLogTool, GitStatusTool, GitWorktreeCreateTool, GitWorktreeListTool, GitWorktreeRemoveTool
from .quality import DiagnosticsTool, FindFilesTool, TestTool
from .understanding import FileMetadataTool, FindDefinitionTool, FindReferencesTool, ListSymbolsTool, ReadRangeTool, SymbolHoverTool
from .background import KillProcessTool, ListProcessesTool, PollProcessTool, ProcessManager, ProcessStatusTool, RunBackgroundTool
from .lsp import LspStatusTool
from ..hooks import Hook, HookIssue, HookRegistry


def build_default_registry(guard) -> ToolRegistry:
    registry = ToolRegistry()
    process_manager = ProcessManager(state_path=guard.root / ".forgecode" / "background-tasks.json")
    registry.register(ListFilesTool(guard))
    registry.register(ReadFileTool(guard))
    registry.register(SearchTool(guard))
    registry.register(WriteFileTool(guard))
    registry.register(ApplyPatchTool(guard))
    registry.register(WorkspaceSummaryTool(guard))
    registry.register(RepositoryMapTool(guard))
    registry.register(ShellTool(guard))
    registry.register(GitStatusTool(guard))
    registry.register(GitDiffTool(guard))
    registry.register(GitLogTool(guard))
    registry.register(GitWorktreeListTool(guard))
    registry.register(GitWorktreeCreateTool(guard))
    registry.register(GitWorktreeRemoveTool(guard))
    registry.register(GitCommitTool(guard))
    registry.register(FindFilesTool(guard))
    registry.register(TestTool(guard))
    registry.register(DiagnosticsTool(guard))
    registry.register(ReadRangeTool(guard))
    registry.register(ListSymbolsTool(guard))
    registry.register(FileMetadataTool(guard))
    registry.register(FindDefinitionTool())
    registry.register(FindReferencesTool())
    registry.register(SymbolHoverTool())
    registry.register(RunBackgroundTool(guard, process_manager))
    registry.register(ProcessStatusTool(guard, process_manager))
    registry.register(ListProcessesTool(guard, process_manager))
    registry.register(PollProcessTool(guard, process_manager))
    registry.register(KillProcessTool(guard, process_manager))
    registry.register(LspStatusTool())
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
    "PauseRequested",
    "DenyAllApproval",
    "InteractiveApproval",
    "RiskScopedApproval",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "Hook",
    "HookIssue",
    "HookRegistry",
    "GitDiffTool",
    "GitCommitTool",
    "GitLogTool",
    "GitStatusTool",
    "GitWorktreeListTool",
    "GitWorktreeCreateTool",
    "GitWorktreeRemoveTool",
    "DiagnosticsTool",
    "FindFilesTool",
    "TestTool",
    "FileMetadataTool",
    "ListSymbolsTool",
    "ReadRangeTool",
    "FindDefinitionTool",
    "FindReferencesTool",
    "SymbolHoverTool",
    "KillProcessTool",
    "PollProcessTool",
    "ProcessManager",
    "ProcessStatusTool",
    "RunBackgroundTool",
    "ListProcessesTool",
    "LspStatusTool",
    "build_default_registry",
]
