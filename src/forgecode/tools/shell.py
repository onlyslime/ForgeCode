"""Controlled command execution with an explicit approval policy."""

import os
import re
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..security.redaction import redact_value
from .base import ApprovalPolicy, ToolContext, ToolDefinition, ToolResult


_MAX_COMMAND_OUTPUT_CHARS = 20_000
_MAX_COMMAND_CHARS = 4_000


_HARD_BLOCK_PATTERNS = (
    (re.compile(r"\b(?:shutdown|reboot|poweroff|halt|stop-computer|restart-computer)\b", re.I), "system power command", "privilege_or_system"),
    (re.compile(r"\bformat(?:\.com)?\b", re.I), "disk format command", "privilege_or_system"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), "irreversible git reset", "repository_irreversible"),
    (re.compile(r"\bgit\s+clean\s+-[a-z]*f[a-z]*\b", re.I), "irreversible git clean", "repository_irreversible"),
    (re.compile(r"\bgit\s+push\s+[^\n]*--force(?:-with-lease)?\b", re.I), "force push", "repository_irreversible"),
    (
        re.compile(
            r"(?:rm\s+(?:-[a-z]*r[a-z]*f\s+|--\s+)/(?=\s|$)|"
            r"(?:del|rd|rmdir)\s+/s\s+/q\s+[a-z]:\\?$|"
            r"remove-item\s+[^\n]*\b[a-z]:\\?(?:\s|$))",
            re.I,
        ),
        "root filesystem deletion",
        "filesystem_destructive",
    ),
)


def classify_command(command: str) -> tuple[str, tuple[str, ...], bool]:
    """Return (risk class, reasons, hard_blocked) using conservative heuristics."""
    lowered = command.lower()
    for pattern, reason, category in _HARD_BLOCK_PATTERNS:
        if pattern.search(command):
            return category, (reason,), True
    reasons: list[str] = []
    risk = "normal"
    checks = (
        ("filesystem_destructive", (r"\brm\b", r"\bdel\b", r"\brmdir\b", r"remove-item", r"\btruncate\b", r"\bmv\b", r"\bmove\b", r"\bmkdir\b.*-p"), "filesystem mutation/deletion"),
        ("privilege_or_system", (r"\bsudo\b", r"\brunas\b", r"verb\s+runas", r"\bchown\b", r"\bchmod\b.*777"), "privilege or system access"),
        ("network_or_remote", (r"\bcurl\b", r"\bwget\b", r"invoke-webrequest", r"\bssh\b", r"\bscp\b", r"\bgit\s+clone\b", r"\bpip\s+install\b", r"\bnpm\s+install\b"), "network, remote access, or dependency installation"),
        ("repository_irreversible", (r"\bgit\s+(?:checkout|restore|revert|commit|push)\b",), "repository history or state change"),
    )
    for candidate, patterns, reason in checks:
        if any(re.search(pattern, lowered) for pattern in patterns):
            if risk == "normal" or candidate == "privilege_or_system":
                risk = candidate
            reasons.append(reason)
    return risk, tuple(dict.fromkeys(reasons)), False


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _bounded(value: str, limit: int = _MAX_COMMAND_OUTPUT_CHARS) -> tuple[str, bool]:
    return (value[:limit] + ("\n[output truncated]" if len(value) > limit else ""), len(value) > limit)


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Best-effort process-tree termination after a timeout."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


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
        side_effecting=True,
    )

    def __init__(self, guard, approval: ApprovalPolicy | None = None):
        self.guard = guard
        self.approval = approval or DenyAllApproval()

    def execute(self, arguments, context):
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        if len(command) > _MAX_COMMAND_CHARS:
            raise ValueError(f"command exceeds the {_MAX_COMMAND_CHARS}-character safety limit")
        risk, reasons, hard_blocked = classify_command(command)
        command_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        risk_metadata = {"command_id": command_id, "risk": risk, "risk_reasons": list(reasons), "hard_blocked": hard_blocked, "cwd": "."}
        if hard_blocked:
            return ToolResult(False, f"command blocked by safety policy ({'; '.join(reasons)})", {"error": "risk_blocked", **risk_metadata, "command": command})
        approval = context.approval or self.approval
        approval_arguments = {**arguments, "_risk": risk, "_risk_reasons": list(reasons)}
        if context.approval is not None:
            approved = context.request_approval(self.definition.name, approval_arguments)
        else:
            approved = approval.approve(self.definition.name, approval_arguments)
        if not approved:
            return ToolResult(False, "run_command denied by approval policy", {"error": "approval_denied", "approval": "denied", **risk_metadata})
        timeout_value = arguments.get("timeout_seconds", 30)
        if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
            raise ValueError("timeout_seconds must be a number")
        timeout = float(timeout_value)
        if not 1 <= timeout <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        effective_timeout = context.remaining_seconds(timeout)
        if effective_timeout <= 0:
            return ToolResult(False, "command skipped because the run deadline has expired", {"error": "deadline_exceeded", "timed_out": True, "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), **risk_metadata})
        environment = {
            name: value
            for name, value in os.environ.items()
            if not any(marker in name.upper() for marker in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE"))
        }
        process_options: dict[str, Any] = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True
        process: subprocess.Popen | None = None
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=context.guard.root,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                **process_options,
            )
            deadline = time.monotonic() + effective_timeout
            while True:
                if context.cancelled:
                    _terminate_process_tree(process)
                    try:
                        final_stdout, final_stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        final_stdout, final_stderr = "", ""
                    stdout, stdout_truncated = _bounded(_text(final_stdout))
                    stderr, stderr_truncated = _bounded(_text(final_stderr))
                    return ToolResult(False, f"[stdout]\n{stdout}\n[stderr]\n{stderr}\ncommand cancelled", {"error": "cancelled", "timed_out": False, "termination_result": "requested", "command": command, "approval": "approved", "stdout": stdout, "stderr": stderr, "exit_code": process.returncode, "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), "truncated": stdout_truncated or stderr_truncated, **risk_metadata})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, effective_timeout)
                try:
                    stdout_value, stderr_value = process.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except subprocess.TimeoutExpired as exc:
            assert process is not None
            _terminate_process_tree(process)
            try:
                final_stdout, final_stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                final_stdout, final_stderr = "", ""
            stdout, stdout_truncated = _bounded(_text(final_stdout) or _text(exc.stdout))
            stderr, stderr_truncated = _bounded(_text(final_stderr) or _text(exc.stderr))
            output = f"[stdout]\n{stdout}\n[stderr]\n{stderr}\ncommand timed out after {effective_timeout:g}s"
            return ToolResult(False, output, {"error": "timeout", "timed_out": True, "termination_result": "requested", "command": command, "approval": "approved", "stdout": stdout, "stderr": stderr, "exit_code": process.returncode, "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), "truncated": stdout_truncated or stderr_truncated, **risk_metadata})
        except OSError as exc:
            return ToolResult(False, f"command could not start: {exc}", {"error": "execution_error", "command": command, "approval": "approved", "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), **risk_metadata})
        stdout, stdout_truncated = _bounded(_text(stdout_value))
        stderr, stderr_truncated = _bounded(_text(stderr_value))
        output = f"[stdout]\n{stdout}\n[stderr]\n{stderr}"
        return ToolResult(process.returncode == 0, output, {"command": command, "exit_code": process.returncode, "approval": "approved", "mutated": False, "stdout": stdout, "stderr": stderr, "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), "truncated": stdout_truncated or stderr_truncated, **risk_metadata})


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
    return repr(redact_value(values, secrets))
