"""Shared conservative policy for files that may enter model context."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from .security.workspace import WorkspaceGuard


_PRIVATE_PARTS = {
    ".git",
    ".forgecode",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "tmp",
    "temp",
    "sessions",
    "checkpoints",
    "transactions",
    "goals",
    "cache",
}
_PRIVATE_NAMES = {"credentials.json", "id_rsa", "id_ed25519", "authorized_keys"}
_PRIVATE_SUFFIXES = (".pem", ".key", ".secret", ".p12", ".pfx", ".crt", ".cer")


def is_sensitive_context_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/").strip("/")
    parts = [part.lower() for part in normalized.split("/") if part]
    name = parts[-1] if parts else ""
    if any(part in _PRIVATE_PARTS for part in parts):
        return True
    if name.startswith(".env") or name in _PRIVATE_NAMES:
        return True
    return name.endswith(_PRIVATE_SUFFIXES)


def _matches_gitignore(relative: str, name: str, raw_pattern: str) -> bool:
    pattern = raw_pattern.replace("\\", "/")
    directory_only = pattern.endswith("/")
    anchored = pattern.startswith("/")
    normalized = pattern.strip("/")
    if not normalized:
        return False
    if "/" in normalized or anchored:
        matched = fnmatch.fnmatchcase(relative, normalized)
        if directory_only:
            matched = matched or relative.startswith(normalized + "/")
        return matched
    parts = relative.split("/")
    return fnmatch.fnmatchcase(name, normalized) or any(fnmatch.fnmatchcase(part, normalized) for part in parts)


def is_ignored_context_path(guard: WorkspaceGuard, path: Path) -> bool:
    """Return whether a path must be omitted from any model context source.

    Built-in private exclusions cannot be negated.  A small, deterministic
    subset of .gitignore is applied in order, including ordinary ``!``
    negation, which is sufficient for the repository/reference scanners and
    deliberately avoids invoking Git for every candidate.
    """
    try:
        relative = guard.relative(path)
    except (OSError, ValueError):
        return True
    relative = relative.replace("\\", "/")
    if is_sensitive_context_path(relative):
        return True
    gitignore = guard.root / ".gitignore"
    try:
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    ignored = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        negated = stripped.startswith("!")
        pattern = stripped[1:] if negated else stripped
        if pattern and _matches_gitignore(relative, path.name, pattern):
            ignored = not negated
    return ignored


__all__ = ["is_ignored_context_path", "is_sensitive_context_path"]
