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
from .base import ApprovalPolicy, PauseRequested, ToolContext, ToolDefinition, ToolResult


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


def _terminate_process_tree(process: subprocess.Popen) -> bool:
    """Best-effort process-tree termination after a timeout.

    Return whether the process is known to have exited.  A detached/unresolved
    process is never represented as a successful command result.
    """
    if process.poll() is not None:
        return True
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return process.poll() is not None
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return process.poll() is not None
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return process.poll() is not None


class DenyAllApproval:
    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return False


class AllowAllApproval:
    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return True


class RiskScopedApproval:
    """Apply per-risk-domain decisions before delegating to a fallback policy."""
    GROUPS = {
        "changes": frozenset({"write_file", "apply_patch", "git_commit"}),
        "execution": frozenset({"run_command", "run_background", "kill_process"}),
        "evidence": frozenset({"test", "diagnostics"}),
    }

    def __init__(self, fallback: ApprovalPolicy, decisions: dict[str, str] | None = None):
        self.fallback = fallback
        self.decisions = dict(decisions or {})
        if set(self.decisions) - set(self.GROUPS) or any(v not in {"allow", "ask", "deny"} for v in self.decisions.values()):
            raise ValueError("approval decisions must map groups to allow, ask, or deny")

    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        for group, tools in self.GROUPS.items():
            if tool_name in tools and group in self.decisions:
                decision = self.decisions[group]
                if decision == "allow": return True
                if decision == "deny": return False
        return self.fallback.approve(tool_name, arguments)


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
        if context.cancelled:
            return ToolResult(False, "run_command cancelled before execution", {"error": "cancelled", "approval": "not_requested", "termination_result": "not_started", "cancellation_reason": context.cancellation_reason, **risk_metadata})
        approval_arguments = {**arguments, "_risk": risk, "_risk_reasons": list(reasons)}
        if context.approval is not None:
            approved = context.request_approval(self.definition.name, approval_arguments)
        else:
            approved = approval.approve(self.definition.name, approval_arguments)
        if not approved:
            return ToolResult(False, "run_command denied by approval policy", {"error": "approval_denied", "approval": "denied", **risk_metadata})
        if context.cancelled:
            return ToolResult(False, "run_command cancelled after approval", {"error": "cancelled", "approval": "approved", "termination_result": "not_started", "cancellation_reason": context.cancellation_reason, **risk_metadata})
        stale = context.deny_if_stale(self.definition.name)
        if stale:
            return stale
        if context.cancelled:
            return ToolResult(False, "run_command cancelled before process start", {"error": "cancelled", "approval": "approved", "termination_result": "not_started", "cancellation_reason": context.cancellation_reason, **risk_metadata})
        timeout_value = arguments.get("timeout_seconds", 30)
        if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
            raise ValueError("timeout_seconds must be a number")
        timeout = float(timeout_value)
        if not 1 <= timeout <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        effective_timeout = context.remaining_seconds(timeout)
        if effective_timeout <= 0:
            return ToolResult(False, "command skipped because the run deadline has expired", {"error": "deadline_exceeded", "timed_out": True, "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), **risk_metadata})
        if context.cancelled:
            return ToolResult(False, "run_command cancelled before process start", {"error": "cancelled", "approval": "approved", "termination_result": "not_started", "cancellation_reason": context.cancellation_reason, **risk_metadata})
        if context.pause_wait is not None:
            try:
                context.pause_wait()
            except PauseRequested:
                return ToolResult(False, "run_command paused before process start", {"error": "paused", "approval": "approved", "termination_result": "not_started", **risk_metadata})
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
            if context.cancelled:
                terminated = _terminate_process_tree(process)
                try:
                    final_stdout, final_stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    final_stdout, final_stderr = "", ""
                stdout, stdout_truncated = _bounded(_text(final_stdout))
                stderr, stderr_truncated = _bounded(_text(final_stderr))
                return ToolResult(False, f"[stdout]\n{stdout}\n[stderr]\n{stderr}\ncommand cancelled", {"error": "cancelled", "timed_out": False, "termination_result": "requested" if terminated else "unresolved", "cancellation_reason": context.cancellation_reason, "command": command, "approval": "approved", "stdout": stdout, "stderr": stderr, "exit_code": process.returncode, "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), "truncated": stdout_truncated or stderr_truncated, **risk_metadata})
            deadline = time.monotonic() + effective_timeout
            while True:
                if context.cancelled:
                    terminated = _terminate_process_tree(process)
                    try:
                        final_stdout, final_stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        final_stdout, final_stderr = "", ""
                    stdout, stdout_truncated = _bounded(_text(final_stdout))
                    stderr, stderr_truncated = _bounded(_text(final_stderr))
                    return ToolResult(False, f"[stdout]\n{stdout}\n[stderr]\n{stderr}\ncommand cancelled", {"error": "cancelled", "timed_out": False, "termination_result": "requested" if terminated else "unresolved", "cancellation_reason": context.cancellation_reason, "command": command, "approval": "approved", "stdout": stdout, "stderr": stderr, "exit_code": process.returncode, "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), "truncated": stdout_truncated or stderr_truncated, **risk_metadata})
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
            terminated = _terminate_process_tree(process)
            try:
                final_stdout, final_stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                final_stdout, final_stderr = "", ""
            stdout, stdout_truncated = _bounded(_text(final_stdout) or _text(exc.stdout))
            stderr, stderr_truncated = _bounded(_text(final_stderr) or _text(exc.stderr))
            output = f"[stdout]\n{stdout}\n[stderr]\n{stderr}\ncommand timed out after {effective_timeout:g}s"
            return ToolResult(False, output, {"error": "timeout", "timed_out": True, "termination_result": "requested" if terminated else "unresolved", "command": command, "approval": "approved", "stdout": stdout, "stderr": stderr, "exit_code": process.returncode, "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), "truncated": stdout_truncated or stderr_truncated, **risk_metadata})
        except OSError as exc:
            return ToolResult(False, f"command could not start: {exc}", {"error": "execution_error", "command": command, "approval": "approved", "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), **risk_metadata})
        stdout, stdout_truncated = _bounded(_text(stdout_value))
        stderr, stderr_truncated = _bounded(_text(stderr_value))
        output = f"[stdout]\n{stdout}\n[stderr]\n{stderr}"
        return ToolResult(process.returncode == 0, output, {"command": command, "exit_code": process.returncode, "approval": "approved", "mutated": False, "stdout": stdout, "stderr": stderr, "duration_seconds": round(time.monotonic() - started, 3), "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(), "truncated": stdout_truncated or stderr_truncated, **risk_metadata})


class InteractiveApproval:
    """Ask before side effects, with an explicit opt-in automatic mode."""

    def __init__(self, *, auto_approve: bool = False, input_fn=None, output_fn=None, secrets=(), prompt_to_output: bool = False):
        self.auto_approve = auto_approve
        # Resolve defaults at construction time so CLI/tests can safely
        # inject stdin/stdout (and JSON mode can route prompts to stderr).
        self.input_fn = input if input_fn is None else input_fn
        self.output_fn = print if output_fn is None else output_fn
        self.prompt_to_output = bool(prompt_to_output)
        self.secrets = tuple(secret for secret in secrets if secret)

    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if self.auto_approve:
            return True
        summary = _safe_summary(arguments, self.secrets)
        prompt = f"Approve {tool_name} {summary}? [y/N] "
        try:
            if self.prompt_to_output:
                # ``input(prompt)`` writes the prompt to stdout itself.  JSON
                # callers must render it through their diagnostic channel.
                self.output_fn(prompt)
                answer = self.input_fn("")
            else:
                answer = self.input_fn(prompt)
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
