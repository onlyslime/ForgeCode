from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, GitCommitTool, GitLogTool, ToolContext

def test_git_log_and_commit_require_expected_boundaries(tmp_path):
    guard = WorkspaceGuard(tmp_path)
    context = ToolContext(guard, AllowAllApproval())
    log = GitLogTool(guard).execute({"limit": 5}, context)
    assert not log.ok
    commit = GitCommitTool(guard).execute({"message": "demo"}, context)
    assert not commit.ok
    assert commit.metadata.get("error") in {"git_commit_failed", "nothing_staged"} or commit.metadata.get("exit_code") == 128
