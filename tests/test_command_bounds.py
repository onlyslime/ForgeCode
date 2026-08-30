import time
from pathlib import Path

from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry
from forgecode.tools.shell import classify_command


def test_command_classifier_handles_shell_and_git_variants():
    blocked = ["GIT  RESET   --HARD HEAD", "git clean -xfd", "git push origin main --force-with-lease", "git push origin --mirror", "git push origin --delete main", "git -C repo push origin +main", "git --git-dir=.git push origin +main", "git -c core.sshCommand=ssh push origin +main", "shutdown /s", "sudo reboot"]
    for command in blocked:
        risk, reasons, hard = classify_command(command)
        assert hard and reasons and risk in {"privilege_or_system", "repository_irreversible"}
    assert classify_command("python -m pytest -q")[0] == "normal"
    assert classify_command("Invoke-WebRequest https://example.test")[0] == "network_or_remote"


def test_command_output_is_bounded_and_metadata_complete(tmp_path: Path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    result = registry.execute("run_command", {"command": "python -c \"print('x'*50000)\""}, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))
    assert result.ok
    assert result.metadata["command_id"] and result.metadata["cwd"] == "."
    assert result.metadata["truncated"] is True
    assert len(result.metadata["stdout"]) <= 20_020
    assert result.metadata["started_at"] and result.metadata["ended_at"]


def test_command_honors_run_deadline(tmp_path: Path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval(), deadline_monotonic=time.monotonic() + 0.05)
    result = registry.execute("run_command", {"command": "python -c \"import time; time.sleep(3)\"", "timeout_seconds": 30}, context)
    assert not result.ok and result.metadata["timed_out"] is True


def test_command_cancellation_terminates_process(tmp_path: Path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    cancelled = False
    started = time.monotonic()

    def is_cancelled():
        return time.monotonic() - started > 0.4

    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval(), cancellation_requested=is_cancelled)
    result = registry.execute("run_command", {"command": "python -c \"import time; time.sleep(3)\"", "timeout_seconds": 10}, context)
    assert not result.ok and result.metadata["error"] == "cancelled"
