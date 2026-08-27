"""Workspace path validation used by every filesystem tool."""

from pathlib import Path


class WorkspaceViolation(ValueError):
    """Raised when a path escapes the configured workspace."""


class WorkspaceGuard:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceViolation(f"workspace does not exist: {self.root}")

    def resolve(self, user_path: str | Path, *, must_exist: bool = False) -> Path:
        candidate = Path(user_path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.expanduser().resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise WorkspaceViolation(f"path is outside workspace: {user_path}")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(resolved)
        return resolved

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()
