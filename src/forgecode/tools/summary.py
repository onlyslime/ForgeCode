"""Deterministic, bounded workspace metadata for initial agent context."""

from __future__ import annotations

import subprocess
from typing import Any

from .base import ToolContext, ToolDefinition, ToolResult
from .filesystem import _is_ignored, _SKIP_DIRS


_LANGUAGE_SUFFIXES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/React",
    ".jsx": "JavaScript/React",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C/C++ header",
    ".cs": "C#",
}
_BUILD_FILES = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Makefile")
_TEST_DIRS = ("tests", "test", "__tests__")
_MAX_TOP_LEVEL = 100
_MAX_SCANNED_FILES = 2_000
_MAX_GIT_STATUS_CHARS = 4_000


def _safe_git_status(text: str) -> tuple[str, bool]:
    """Keep status useful without exposing sensitive filenames or huge output."""
    visible: list[str] = []
    for line in text.splitlines():
        normalized = line.lower()
        if any(token in normalized for token in (".env", "credential", "secret", ".pem", ".key", "api-key")):
            continue
        visible.append(line)
    rendered = "\n".join(visible)
    return rendered[:_MAX_GIT_STATUS_CHARS], len(rendered) > _MAX_GIT_STATUS_CHARS


class WorkspaceSummaryTool:
    definition = ToolDefinition(
        "workspace_summary",
        "Summarize workspace structure, likely languages, build files, tests, and Git status without reading source contents.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        root = context.guard.root
        top_level = []
        language_counts: dict[str, int] = {}
        for path in sorted(root.iterdir(), key=lambda candidate: candidate.name.lower()):
            if _is_ignored(path, context.guard):
                continue
            relative = context.guard.relative(path)
            top_level.append(relative + ("/" if path.is_dir() else ""))
        scanned = 0
        scan_truncated = False
        for path in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
            if scanned >= 2_000:
                scan_truncated = True
                break
            if _is_ignored(path, context.guard) or not path.is_file():
                continue
            scanned += 1
            language = _LANGUAGE_SUFFIXES.get(path.suffix.lower())
            if language:
                language_counts[language] = language_counts.get(language, 0) + 1
        build_files = [name for name in _BUILD_FILES if (root / name).is_file() and not _is_ignored(root / name, context.guard)]
        test_dirs = [name for name in _TEST_DIRS if (root / name).is_dir() and not _is_ignored(root / name, context.guard)]
        git_status = "not a Git repository"
        if (root / ".git").exists():
            try:
                completed = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if completed.returncode == 0:
                    git_status, git_status_truncated = _safe_git_status(completed.stdout.strip())
                else:
                    git_status, git_status_truncated = "Git status unavailable", False
                if not git_status:
                    git_status = "clean"
            except (OSError, subprocess.TimeoutExpired):
                git_status, git_status_truncated = "Git status unavailable", False
        else:
            git_status_truncated = False
        top_level_omitted = max(0, len(top_level) - _MAX_TOP_LEVEL)
        shown_top_level = top_level[:_MAX_TOP_LEVEL]
        lines = [
            f"workspace: {root}",
            "top_level: " + (", ".join(shown_top_level) or "(empty)"),
            "languages: " + (", ".join(f"{name} ({count})" for name, count in sorted(language_counts.items())) or "unknown"),
            "build_files: " + (", ".join(build_files) or "none detected"),
            "test_directories: " + (", ".join(test_dirs) or "none detected"),
            "ignored_directories: " + ", ".join(sorted(_SKIP_DIRS)),
            "git_status:\n" + git_status,
        ]
        if top_level_omitted:
            lines.insert(2, f"top_level_omitted: {top_level_omitted}")
        if scan_truncated:
            lines.insert(4, f"language_scan_truncated_after: {_MAX_SCANNED_FILES}")
        if git_status_truncated:
            lines.append("git_status_truncated: true")
        output = "\n".join(lines)
        return ToolResult(
            True,
            output,
            {
                "top_level_count": len(top_level),
                "top_level_omitted": top_level_omitted,
                "language_files_scanned": scanned,
                "language_scan_truncated": scan_truncated,
                "build_files": build_files,
                "test_directories": test_dirs,
                "git_status": git_status,
                "git_status_truncated": git_status_truncated,
            },
        )
