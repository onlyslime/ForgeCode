import time
from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, ProcessManager, PollProcessTool, RunBackgroundTool, ToolContext

def test_background_task_can_be_started_and_polled(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    manager = ProcessManager()
    started = RunBackgroundTool(guard, manager).execute({"command": "python -c \"print('hello')\""}, context)
    assert started.ok
    task_id = started.metadata["task_id"]
    for _ in range(20):
        result = PollProcessTool(guard, manager).execute({"task_id": task_id}, context)
        if result.metadata.get("status") != "running": break
        time.sleep(0.05)
    assert "hello" in result.output


def test_background_output_is_hard_bounded_and_completion_duration_stable(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    manager = ProcessManager(max_output_chars=12)
    started = RunBackgroundTool(guard, manager).execute({"command": "python -c \"print('abcdefghijklmnop')\""}, context)
    task_id = started.metadata["task_id"]
    for _ in range(30):
        result = PollProcessTool(guard, manager).execute({"task_id": task_id}, context)
        if result.metadata.get("status") != "running": break
        time.sleep(0.03)
    assert len(result.metadata["output"]) <= 12
    assert result.metadata["truncated"] is True
    first_duration = result.metadata["duration_seconds"]
    time.sleep(0.05)
    later = PollProcessTool(guard, manager).execute({"task_id": task_id}, context)
    assert later.metadata["duration_seconds"] == first_duration
