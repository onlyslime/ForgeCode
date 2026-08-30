import time
from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, KillProcessTool, ListProcessesTool, ProcessManager, PollProcessTool, RunBackgroundTool, ToolContext

def test_background_task_can_be_started_and_polled(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    manager = ProcessManager()
    started = RunBackgroundTool(guard, manager).execute({"command": "python -c \"print('hello')\""}, context)
    assert started.ok
    assert "command" not in started.metadata
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

def test_background_processes_can_be_listed_without_output_or_secret_command(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    manager = ProcessManager()
    started = RunBackgroundTool(guard, manager).execute({"command": "python -c \"print('x')\""}, context)
    rows = ListProcessesTool(guard, manager).execute({}, context)
    assert rows.metadata["count"] >= 1
    assert started.metadata["task_id"] in {row["task_id"] for row in rows.metadata["tasks"]}
    assert all("output" not in row and "command" not in row for row in rows.metadata["tasks"])

def test_background_process_list_never_exposes_command_arguments(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    manager = ProcessManager()
    RunBackgroundTool(guard, manager).execute({"command": "python -c \"print('secret-token')\""}, context)
    result = ListProcessesTool(guard, manager).execute({}, context)
    assert "secret-token" not in result.output
    assert all("command" not in row for row in result.metadata["tasks"])

def test_background_history_is_bounded_without_evicting_active_tasks(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    manager = ProcessManager(max_history=2)
    first = RunBackgroundTool(guard, manager).execute({"command": "python -c \"print(1)\""}, context)
    second = RunBackgroundTool(guard, manager).execute({"command": "python -c \"print(2)\""}, context)
    time.sleep(0.1)
    third = RunBackgroundTool(guard, manager).execute({"command": "python -c \"import time; time.sleep(0.3)\""}, context)
    assert manager.get(third.metadata["task_id"]) is not None
    assert len(manager.list()) <= 2

def test_kill_process_reports_confirmed_termination(tmp_path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    manager = ProcessManager()
    started = RunBackgroundTool(guard, manager).execute({"command": "python -c \"import time; time.sleep(10)\""}, context)
    result = KillProcessTool(guard, manager).execute({"task_id": started.metadata["task_id"]}, context)
    assert result.ok and result.metadata["termination_result"] in {"confirmed", "already_exited"}
