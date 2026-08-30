import time
import os
from pathlib import Path

from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry
from forgecode.tools.shell import classify_command
from forgecode.tools.quality import DiagnosticsTool, TestTool as QualityCheckTool
from forgecode.tools.filesystem import ListFilesTool, ReadFileTool, SearchTool, WriteFileTool
from forgecode.tools.repository_map import RepositoryMapTool
from forgecode.tools.understanding import ReadRangeTool, ListSymbolsTool, FindDefinitionTool, FindReferencesTool, SymbolHoverTool, FileMetadataTool
from forgecode.tools.lsp import LspStatusTool
from forgecode.tools.summary import WorkspaceSummaryTool
import pytest


def test_command_classifier_handles_shell_and_git_variants():
    blocked = ["GIT  RESET   --HARD HEAD", "git clean -xfd", "git push origin main --force-with-lease", "git push origin --mirror", "git push origin --delete main", "git push -f origin main", "git push -fd origin main", "git push -d origin main", "git -C repo push origin +main", "git --git-dir=.git push origin +main", "git -c core.sshCommand=ssh push origin +main", "shutdown /s", "sudo reboot"]
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


def test_quality_tools_reject_empty_explicit_commands(tmp_path: Path):
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    for tool in (QualityCheckTool(tmp_path), DiagnosticsTool(tmp_path)):
        with pytest.raises(ValueError, match="non-empty"):
            tool.execute({"command": "   "}, context)


def test_quality_tools_reject_non_object_arguments(tmp_path: Path):
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    for tool in (QualityCheckTool(tmp_path), DiagnosticsTool(tmp_path)):
        with pytest.raises(ValueError, match="arguments must be an object"):
            tool.execute(None, context)


def test_quality_tools_apply_command_safety_classification(tmp_path: Path):
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    for tool in (QualityCheckTool(tmp_path), DiagnosticsTool(tmp_path)):
        result = tool.execute({"command": "git reset --hard HEAD"}, context)
        assert result.ok is False
        assert result.metadata["error"] == "risk_blocked"
        assert result.metadata["hard_blocked"] is True


def test_quality_tools_do_not_forward_sensitive_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGECODE_TEST_SECRET", "hidden-value")
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    result = DiagnosticsTool(tmp_path).execute({"command": "python -c \"import os; print(os.getenv('FORGECODE_TEST_SECRET', 'absent'))\""}, context)
    assert result.ok
    assert "hidden-value" not in result.output
    assert "absent" in result.output


def test_quality_tool_denials_preserve_risk_metadata(tmp_path: Path):
    class Deny:
        def approve(self, *_args):
            return False
    context = ToolContext(WorkspaceGuard(tmp_path), Deny())
    result = DiagnosticsTool(tmp_path).execute({"command": "python -m compileall"}, context)
    assert result.ok is False
    assert result.metadata["error"] == "approval_denied"
    assert result.metadata["risk"] == "normal"
    assert result.metadata["hard_blocked"] is False


def test_filesystem_tools_reject_non_object_arguments(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    tools = [ListFilesTool(guard), ReadFileTool(guard), SearchTool(guard), WriteFileTool(guard)]
    for tool in tools:
        with pytest.raises(ValueError, match="arguments must be an object"):
            tool.execute(None, context)


def test_repository_map_budget_is_bounded(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    with pytest.raises(ValueError, match="between 256 and 100000"):
        RepositoryMapTool(guard).execute({"budget_chars": 100001}, context)
    schema = RepositoryMapTool(guard).definition.parameters
    assert schema["properties"]["budget_chars"]["maximum"] == 100_000
    with pytest.raises(ValueError, match="arguments must be an object"):
        RepositoryMapTool(guard).execute(None, context)


def test_understanding_tools_reject_non_object_arguments(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    tools = [ReadRangeTool(guard), ListSymbolsTool(guard), FindDefinitionTool(), FindReferencesTool(), SymbolHoverTool(), FileMetadataTool(guard)]
    for tool in tools:
        with pytest.raises(ValueError, match="arguments must be an object"):
            tool.execute(None, context)


def test_source_file_scan_stops_at_bounded_limit(tmp_path: Path):
    from forgecode.tools.understanding import _source_files
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    for index in range(510):
        (tmp_path / f"file_{index:03d}.py").write_text("x = 1\n", encoding="utf-8")
    files = list(_source_files(context))
    assert len(files) == 500


def test_understanding_path_must_be_string(tmp_path: Path):
    from forgecode.tools.understanding import _source_files
    guard = WorkspaceGuard(tmp_path); context = ToolContext(guard, AllowAllApproval())
    with pytest.raises(ValueError, match="path must be a string"):
        list(_source_files(context, 42))


def test_understanding_source_scan_rejects_symlink_alias(tmp_path: Path):
    outside = tmp_path / "outside.py"; outside.write_text("secret = 1\n", encoding="utf-8")
    alias = tmp_path / "alias.py"
    try:
        alias.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    assert list(_source_files(context, "alias.py")) == []


def test_lsp_status_rejects_non_object_arguments(tmp_path: Path):
    with pytest.raises(ValueError, match="arguments must be an object"):
        LspStatusTool().execute(None, None)


def test_registry_rejects_unknown_schema_fields(tmp_path: Path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    result = registry.execute("git_status", {"unexpected": True}, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))
    assert result.ok is False
    assert result.metadata["error"] == "invalid_arguments"
    assert result.metadata["unknown_fields"] == ["unexpected"]
    required = registry.execute("read_file", {}, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))
    assert required.ok is False
    assert required.metadata["missing_fields"] == ["path"]
    null_required = registry.execute("read_file", {"path": None}, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))
    assert null_required.ok is False
    assert null_required.metadata["missing_fields"] == ["path"]
    non_string_key = registry.execute("git_status", {1: True}, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))
    assert non_string_key.ok is False
    assert non_string_key.metadata["error"] == "invalid_arguments"
    assert "keys must be strings" in non_string_key.output
    malformed_name = registry.execute([], {}, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))
    assert malformed_name.ok is False
    assert malformed_name.metadata["error"] == "invalid_tool_name"


def test_workspace_summary_rejects_non_object_arguments(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    with pytest.raises(ValueError, match="arguments must be an object"):
        WorkspaceSummaryTool(guard).execute(None, None)
