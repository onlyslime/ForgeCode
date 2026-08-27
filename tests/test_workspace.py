from pathlib import Path

import pytest

from forgecode.security import WorkspaceGuard, WorkspaceViolation


def test_workspace_guard_resolves_relative_paths(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    assert guard.resolve("src/main.py") == tmp_path / "src" / "main.py"


def test_workspace_guard_rejects_escape(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    with pytest.raises(WorkspaceViolation):
        guard.resolve("../outside.txt")
