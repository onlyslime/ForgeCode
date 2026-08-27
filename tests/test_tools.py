from pathlib import Path

from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, DenyAllApproval, ToolContext, build_default_registry


def test_filesystem_tools_are_workspace_scoped(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)
    result = registry.execute("search", {"query": "hello"}, ToolContext(guard, DenyAllApproval()))
    assert result.ok
    assert "main.py:1" in result.output


def test_write_requires_approval(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)
    denied = registry.execute("write_file", {"path": "new.txt", "content": "x"}, ToolContext(guard, DenyAllApproval()))
    assert not denied.ok
    assert not (tmp_path / "new.txt").exists()
    allowed = registry.execute("write_file", {"path": "new.txt", "content": "x"}, ToolContext(guard, AllowAllApproval()))
    assert allowed.ok
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "x"
