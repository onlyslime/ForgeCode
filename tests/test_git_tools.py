from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, GitCommitTool, GitLogTool, GitStatusTool, GitDiffTool, ToolContext
import pytest

def test_git_log_and_commit_require_expected_boundaries(tmp_path):
    guard = WorkspaceGuard(tmp_path)
    context = ToolContext(guard, AllowAllApproval())
    log = GitLogTool(guard).execute({"limit": 5}, context)
    assert not log.ok
    commit = GitCommitTool(guard).execute({"message": "demo"}, context)
    assert not commit.ok
    assert commit.metadata.get("error") in {"git_commit_failed", "nothing_staged"} or commit.metadata.get("exit_code") == 128


def test_git_inspection_tools_reject_non_object_arguments(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    for tool in (GitStatusTool(guard), GitDiffTool(guard), GitLogTool(guard)):
        with pytest.raises(ValueError, match="arguments must be an object"):
            tool.execute(None, context)


def test_git_commit_rejects_non_object_arguments(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    with pytest.raises(ValueError, match="arguments must be an object"):
        GitCommitTool(guard).execute(None, context)


def test_git_inspection_rejects_non_boolean_flags(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    with pytest.raises(ValueError, match="porcelain"):
        GitStatusTool(guard).execute({"porcelain": "yes"}, context)
    with pytest.raises(ValueError, match="staged"):
        GitDiffTool(guard).execute({"staged": 1}, context)
