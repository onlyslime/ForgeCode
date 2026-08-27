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
