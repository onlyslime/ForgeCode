"""Controlled command execution with an explicit approval policy."""

import subprocess
from typing import Any

from .base import ApprovalPolicy, ToolContext, ToolDefinition, ToolResult


_MAX_COMMAND_OUTPUT_CHARS = 20_000


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _bounded(value: str, limit: int = _MAX_COMMAND_OUTPUT_CHARS) -> tuple[str, bool]:
    return (value[:limit] + ("\n[output truncated]" if len(value) > limit else ""), len(value) > limit)


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
        if context.approval is not None:
            approved = context.request_approval(self.definition.name, arguments)
        else:
            approved = approval.approve(self.definition.name, arguments)
        if not approved:
            return ToolResult(False, "run_command denied by approval policy", {"error": "approval_denied", "approval": "denied"})
        timeout_value = arguments.get("timeout_seconds", 30)
        if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
            raise ValueError("timeout_seconds must be a number")
        timeout = float(timeout_value)
        if not 1 <= timeout <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        try:
            completed = subprocess.run(command, cwd=context.guard.root, shell=True, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            stdout, stdout_truncated = _bounded(_text(exc.stdout))
            stderr, stderr_truncated = _bounded(_text(exc.stderr))
            output = f"[stdout]\n{stdout}\n[stderr]\n{stderr}\ncommand timed out after {timeout:g}s"
            return ToolResult(False, output, {"error": "timeout", "command": command, "approval": "approved", "stdout": stdout, "stderr": stderr, "truncated": stdout_truncated or stderr_truncated})
        stdout, stdout_truncated = _bounded(_text(completed.stdout))
        stderr, stderr_truncated = _bounded(_text(completed.stderr))
        output = f"[stdout]\n{stdout}\n[stderr]\n{stderr}"
        return ToolResult(completed.returncode == 0, output, {"command": command, "exit_code": completed.returncode, "approval": "approved", "mutated": False, "stdout": stdout, "stderr": stderr, "truncated": stdout_truncated or stderr_truncated})


class InteractiveApproval:
    """Ask before side effects, with an explicit opt-in automatic mode."""

    def __init__(self, *, auto_approve: bool = False, input_fn=input, output_fn=print, secrets=()):
        self.auto_approve = auto_approve
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.secrets = tuple(secret for secret in secrets if secret)

    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if self.auto_approve:
            return True
        summary = _safe_summary(arguments, self.secrets)
        try:
            answer = self.input_fn(f"Approve {tool_name} {summary}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            self.output_fn("approval denied")
            return False
        return answer.strip().lower() in {"y", "yes"}


def _safe_summary(arguments: dict[str, Any], secrets=()) -> str:
    values = {}
    for key, value in arguments.items():
        if key in {"content", "command"} and isinstance(value, str):
            values[key] = value[:120] + ("..." if len(value) > 120 else "")
        else:
            values[key] = value
    rendered = repr(values)
    for secret in secrets:
        rendered = rendered.replace(secret, "[REDACTED]")
    return rendered.replace("Bearer ", "Bearer [REDACTED]")
