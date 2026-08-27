"""Controlled command execution with an explicit approval policy."""

import subprocess
from typing import Any

from .base import ApprovalPolicy, ToolContext, ToolDefinition, ToolResult


class DenyAllApproval:
    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return False


class AllowAllApproval:
    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return True


class ShellTool:
    definition = ToolDefinition(
        "run_command",
        "Run a command in the workspace after approval.",
        {"type": "object", "properties": {"command": {"type": "string"}, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120}}, "required": ["command"]},
    )

    def __init__(self, guard, approval: ApprovalPolicy | None = None):
        self.guard = guard
        self.approval = approval or DenyAllApproval()

    def execute(self, arguments, context):
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        approval = context.approval or self.approval
        if not approval.approve(self.definition.name, arguments):
            return ToolResult(False, "run_command denied by approval policy", {"error": "approval_denied"})
        timeout = float(arguments.get("timeout_seconds", 30))
        if not 1 <= timeout <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        try:
            completed = subprocess.run(command, cwd=context.guard.root, shell=True, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            return ToolResult(False, f"command timed out after {timeout:g}s: {exc}", {"error": "timeout", "command": command})
        output = (completed.stdout or "") + (completed.stderr or "")
        return ToolResult(completed.returncode == 0, output, {"command": command, "exit_code": completed.returncode})
