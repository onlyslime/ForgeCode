"""Bounded, read-only Git inspection tools."""
from __future__ import annotations

import subprocess
from typing import Any

from .base import ToolContext, ToolDefinition, ToolResult


class GitStatusTool:
    definition = ToolDefinition("git_status", "Show concise tracked/untracked changes for the workspace.", {"type": "object", "properties": {"porcelain": {"type": "boolean"}}, "additionalProperties": False})

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        command = ["git", "status", "--short"] if arguments.get("porcelain", True) else ["git", "status"]
        try:
            result = subprocess.run(command, cwd=context.guard.root, capture_output=True, text=True, timeout=min(15.0, context.remaining_seconds(15.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git status failed: {type(exc).__name__}", {"error": "git_status_failed"})
        if result.returncode != 0:
            return ToolResult(False, result.stderr.strip()[:4_000] or "not a Git repository", {"exit_code": result.returncode})
        return ToolResult(True, result.stdout[:4_000] or "working tree clean", {"exit_code": 0})


class GitDiffTool:
    definition = ToolDefinition("git_diff", "Show a bounded Git diff for review before or after edits.", {"type": "object", "properties": {"staged": {"type": "boolean"}, "path": {"type": "string"}}, "additionalProperties": False})

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        command = ["git", "diff", "--no-ext-diff", "--unified=3"]
        if arguments.get("staged"):
            command.append("--cached")
        path = arguments.get("path")
        if path:
            command.extend(["--", str(context.guard.relative(context.guard.resolve(str(path))))])
        try:
            result = subprocess.run(command, cwd=context.guard.root, capture_output=True, text=True, timeout=min(20.0, context.remaining_seconds(20.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git diff failed: {type(exc).__name__}", {"error": "git_diff_failed"})
        if result.returncode != 0:
            return ToolResult(False, result.stderr.strip()[:4_000], {"exit_code": result.returncode})
        return ToolResult(True, result.stdout[:20_000] or "no differences", {"exit_code": 0})
