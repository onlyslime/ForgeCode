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


class GitLogTool:
    definition = ToolDefinition("git_log", "Show recent commit history with hash, subject, author, and date.", {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False})

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        limit = arguments.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        try:
            result = subprocess.run(["git", "log", f"-{limit}", "--date=short", "--pretty=format:%h %ad %an %s"], cwd=context.guard.root, capture_output=True, text=True, timeout=min(15.0, context.remaining_seconds(15.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git log failed: {type(exc).__name__}", {"error": "git_log_failed"})
        if result.returncode != 0:
            return ToolResult(False, result.stderr.strip()[:4_000], {"exit_code": result.returncode})
        return ToolResult(True, result.stdout[:8_000] or "no commits", {"count": len(result.stdout.splitlines()), "exit_code": 0})


class GitCommitTool:
    definition = ToolDefinition("git_commit", "Create a Git commit for current changes after explicit approval.", {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False}, side_effecting=True)

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 200:
            raise ValueError("commit message must be 1-200 characters")
        if not context.request_approval(self.definition.name, {"message": message}):
            return ToolResult(False, "git_commit denied by approval policy", {"error": "approval_denied"})
        if context.cancelled:
            return ToolResult(False, "git_commit cancelled before execution", {"error": "cancelled"})
        try:
            result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=context.guard.root, capture_output=True, text=True, timeout=min(15.0, context.remaining_seconds(15.0)), check=False)
            if result.returncode == 0:
                return ToolResult(False, "no staged changes; stage files before git_commit", {"error": "nothing_staged", "exit_code": 0})
            result = subprocess.run(["git", "commit", "-m", message], cwd=context.guard.root, capture_output=True, text=True, timeout=min(30.0, context.remaining_seconds(30.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git commit failed: {type(exc).__name__}", {"error": "git_commit_failed"})
        output = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()[:8_000]
        return ToolResult(result.returncode == 0, output or ("commit created" if result.returncode == 0 else "commit failed"), {"exit_code": result.returncode})
