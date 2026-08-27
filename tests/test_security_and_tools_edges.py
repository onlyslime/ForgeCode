import subprocess
from pathlib import Path

import pytest

from forgecode.security import WorkspaceGuard, WorkspaceViolation
from forgecode.tools import AllowAllApproval, DenyAllApproval, ToolContext, ToolRegistry, ToolResult, build_default_registry


def test_absolute_outside_and_symlink_escape_are_rejected(tmp_path):
    guard = WorkspaceGuard(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    with pytest.raises(WorkspaceViolation):
        guard.resolve(outside)
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(WorkspaceViolation):
        guard.resolve("link/escape.txt")


def test_search_is_literal_by_default_and_regex_when_requested(tmp_path):
    (tmp_path / "a.txt").write_text("a.b\naxb\n", encoding="utf-8")
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), DenyAllApproval())
    literal = registry.execute("search", {"query": "a.b"}, context)
    regex = registry.execute("search", {"query": "a.b", "regex": True}, context)
    assert "a.txt:1" in literal.output
    assert "a.txt:2" not in literal.output
    assert "a.txt:2" in regex.output


def test_command_timeout_preserves_structured_result(tmp_path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    result = registry.execute("run_command", {"command": "python -c \"import time; time.sleep(2)\"", "timeout_seconds": 1}, context)
    assert not result.ok
    assert result.metadata["error"] == "timeout"
    assert "timed out" in result.output
    assert result.metadata["timed_out"] is True
    assert result.metadata["duration_seconds"] >= 0


def test_command_risk_classification_and_hard_block(tmp_path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    network = registry.execute("run_command", {"command": "curl https://example.test"}, context)
    assert network.metadata["risk"] == "network_or_remote"
    assert network.metadata["risk_reasons"]
    blocked = registry.execute("run_command", {"command": "git reset --hard HEAD"}, context)
    assert not blocked.ok
    assert blocked.metadata["error"] == "risk_blocked"
    assert blocked.metadata["hard_blocked"] is True


def test_command_hard_blocks_windows_power_and_root_deletion_variants(tmp_path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    for command in ("Restart-Computer -Force", "Remove-Item -Recurse -Force C:\\", "rm -- /"):
        result = registry.execute("run_command", {"command": command}, context)
        assert not result.ok
        assert result.metadata["error"] == "risk_blocked"
        assert result.metadata["hard_blocked"] is True
    assert registry.execute("run_command", {"command": "rm -- /"}, context).metadata["risk"] == "filesystem_destructive"


def test_command_risk_classes_are_visible_when_denied(tmp_path):
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), DenyAllApproval())
    cases = {
        "rm -rf temporary-dir": "filesystem_destructive",
        "curl https://example.test": "network_or_remote",
        "git checkout -- file.txt": "repository_irreversible",
    }
    for command, expected in cases.items():
        result = registry.execute("run_command", {"command": command}, context)
        assert not result.ok
        assert result.metadata["error"] == "approval_denied"
        assert result.metadata["risk"] == expected


def test_command_scrubs_secret_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGECODE_API_KEY", "do-not-leak")
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    result = registry.execute("run_command", {"command": "python -c \"import os; print(os.getenv('FORGECODE_API_KEY', 'missing'))\""}, context)
    assert result.ok
    assert "do-not-leak" not in result.output
    assert "missing" in result.output


def test_workspace_summary_is_bounded_and_does_not_expose_env_contents(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / ".env").write_text("TOP_SECRET=do-not-read\n", encoding="utf-8")
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    result = registry.execute("workspace_summary", {}, ToolContext(WorkspaceGuard(tmp_path), DenyAllApproval()))
    assert result.ok
    assert "pyproject.toml" in result.output
    assert "do-not-read" not in result.output
    assert ".env" not in result.output


def test_list_and_search_report_omitted_results(tmp_path):
    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text("match\n", encoding="utf-8")
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), DenyAllApproval())
    listed = registry.execute("list_files", {"pattern": "*.txt", "max_files": 2}, context)
    assert listed.metadata["omitted"] == 3
    assert "3 files omitted" in listed.output
    searched = registry.execute("search", {"query": "match", "max_matches": 2}, context)
    assert searched.metadata["omitted_at_least"] == 3
    assert "matches omitted" in searched.output


def test_tool_numeric_arguments_are_strict(tmp_path):
    (tmp_path / "present.txt").write_text("ok", encoding="utf-8")
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    context = ToolContext(WorkspaceGuard(tmp_path), DenyAllApproval())
    result = registry.execute("read_file", {"path": "present.txt", "max_chars": 1.5}, context)
    assert not result.ok
    assert "integer" in result.output
    result = registry.execute("run_command", {"command": "echo hi", "timeout_seconds": "1"}, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))
    assert not result.ok
    assert "number" in result.output


def test_side_effect_tools_defend_against_direct_plan_calls(tmp_path):
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)
    context = ToolContext(guard, AllowAllApproval(), mode="plan")
    for name, arguments in (
        ("write_file", {"path": "direct.txt", "content": "no"}),
        ("apply_patch", {"patch": "--- a/missing.txt\n+++ b/missing.txt\n@@ -0,0 +1 @@\n+x"}),
        ("run_command", {"command": "python -c \"open('direct.txt','w').write('no')\""}),
    ):
        tool = next(tool for tool in registry._tools.values() if tool.definition.name == name)
        result = tool.execute(arguments, context)
        assert not result.ok
        assert result.metadata["error"] == "mode_denied"
    assert not (tmp_path / "direct.txt").exists()


def test_registry_truncates_tool_output():
    class HugeTool:
        definition = type("Definition", (), {"name": "huge", "description": "", "parameters": {}})()

        def execute(self, arguments, context):
            return ToolResult(True, "x" * 100)

    registry = ToolRegistry(max_output_chars=10)
    registry.register(HugeTool())
    context = ToolContext(WorkspaceGuard(Path.cwd()), DenyAllApproval())
    result = registry.execute("huge", {}, context)
    assert result.metadata["truncated"] is True
    assert len(result.output) < 100
