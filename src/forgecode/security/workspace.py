"""Workspace path validation used by every filesystem tool."""

from pathlib import Path
import os


def _is_reparse_or_symlink(path: Path) -> bool:
    """Return whether *path* is an aliasing filesystem entry.

    ``Path.is_symlink`` covers POSIX links and Windows symbolic links.  A
    Windows junction is represented as a reparse point but is not always
    reported as a symlink, so inspect the file-attribute bit as a best effort
    fallback.  Missing components are valid for create operations, while
    metadata/permission errors are treated as aliases so the guard fails
    closed instead of allowing an unreadable reparse point through.
    """
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & 0x0400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except FileNotFoundError:
        return False
    except OSError:
        return True


def assert_no_path_alias(path: str | Path, *, message: str = "path is a symlink or junction alias") -> Path:
    """Validate a lexical path without following links in existing parents.

    This check closes the common alias case where a link points *inside* the
    workspace: resolving first would otherwise make the path appear safe.
    It is intentionally lexical and bounded to the path's existing parents;
    ordinary filesystem races remain documented limitations of a user-space
    guard.
    """
    lexical = Path(path).expanduser()
    if not lexical.is_absolute():
        lexical = Path.cwd() / lexical
    lexical = lexical.absolute()
    parts = lexical.parts
    if any(part in {"", ".", ".."} for part in parts):
        # ``Path.absolute`` preserves ``..`` on some platforms; reject it
        # explicitly rather than allowing an alias through normalization.
        raise WorkspaceViolation(message)
    current = Path(lexical.anchor)
    for part in parts[1:]:
        current = current / part
        if _is_reparse_or_symlink(current):
            raise WorkspaceViolation(message)
    return lexical


class WorkspaceViolation(ValueError):
    """Raised when a path escapes the configured workspace."""


class WorkspaceGuard:
    def __init__(self, root: Path):
        if not isinstance(root, (Path, os.PathLike)):
            raise TypeError("workspace root must be a path-like object")
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceViolation(f"workspace does not exist: {self.root}")

    def resolve(self, user_path: str | Path, *, must_exist: bool = False) -> Path:
        candidate = Path(user_path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        lexical = assert_no_path_alias(candidate)
        resolved = lexical.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise WorkspaceViolation(f"path is outside workspace: {user_path}")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(resolved)
        return resolved

    def relative(self, path: Path) -> str:
        lexical = assert_no_path_alias(path)
        return lexical.resolve().relative_to(self.root).as_posix()


__all__ = ["WorkspaceGuard", "WorkspaceViolation", "assert_no_path_alias"]
