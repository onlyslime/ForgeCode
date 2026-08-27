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


def test_alias_that_points_back_inside_is_rejected(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "real"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(WorkspaceViolation, match="symlink or junction"):
        guard.resolve("alias/file.txt")
