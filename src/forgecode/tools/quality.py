"""Bounded project quality checks exposed as first-class tools."""
from __future__ import annotations
import subprocess
import os
from typing import Any
from .base import ToolContext, ToolDefinition, ToolResult
from .filesystem import ListFilesTool
from .shell import classify_command

class FindFilesTool:
    definition = ToolDefinition("find_files", "Find workspace files by glob pattern.", {"type":"object","properties":{"pattern":{"type":"string"},"max_files":{"type":"integer"}},"required":["pattern"]})
    def __init__(self, guard): self._delegate = ListFilesTool(guard)
    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return self._delegate.execute(arguments, context)

class _CheckTool:
    side_effecting = True
    def __init__(self, guard): self.guard = guard
    def _run(self, command: str, context: ToolContext) -> ToolResult:
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        risk, reasons, hard_blocked = classify_command(command)
        risk_metadata = {"risk": risk, "risk_reasons": list(reasons), "hard_blocked": hard_blocked}
        if hard_blocked:
            return ToolResult(False, "command blocked by safety policy", {"error": "risk_blocked", **risk_metadata})
        if not context.request_approval(self.definition.name, {"command": command, "_risk": risk, "_risk_reasons": list(reasons)}):
            return ToolResult(False, f"{self.definition.name} denied by approval policy", {"error": "approval_denied", **risk_metadata})
        if context.cancelled:
            return ToolResult(False, f"{self.definition.name} cancelled before execution", {"error": "cancelled", **risk_metadata})
        if context.remaining_seconds(60.0) <= 0:
            return ToolResult(False, f"{self.definition.name} skipped because the run deadline has expired", {"error": "deadline_exceeded", **risk_metadata})
        try:
            environment = {
                name: value
                for name, value in os.environ.items()
                if not any(marker in name.upper() for marker in ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE"))
            }
            p = subprocess.run(command, cwd=context.guard.root, shell=True, env=environment, capture_output=True, text=True, timeout=min(60.0, context.remaining_seconds(60.0)), check=False)
        except subprocess.TimeoutExpired:
            error = "deadline_exceeded" if context.remaining_seconds(0) <= 0 else "check_timeout"
            return ToolResult(False, f"{self.definition.name} timed out", {"error": error, **risk_metadata})
        except OSError as exc:
            return ToolResult(False, f"check failed: {type(exc).__name__}", {"error": "check_failed"})
        output = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip()[:20_000]
        return ToolResult(p.returncode == 0, output or ("passed" if p.returncode == 0 else "failed"), {"exit_code": p.returncode, **risk_metadata})

class TestTool(_CheckTool):
    definition = ToolDefinition("test", "Run the project's tests using its detected test runner.", {"type":"object","properties":{"command":{"type":"string"}},"additionalProperties":False}, side_effecting=True)
    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        supplied = arguments.get("command")
        command = supplied if supplied is not None else ("pytest -q" if (context.guard.root / "pytest.ini").exists() or (context.guard.root / "tests").is_dir() else "python -m unittest")
        if not isinstance(command, str) or not command.strip() or len(command) > 500: raise ValueError("command must be non-empty bounded text")
        return self._run(command, context)

class DiagnosticsTool(_CheckTool):
    definition = ToolDefinition("diagnostics", "Run bounded compile/type diagnostics for the project.", {"type":"object","properties":{"command":{"type":"string"}},"additionalProperties":False}, side_effecting=True)
    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        root = context.guard.root
        supplied = arguments.get("command")
        command = supplied if supplied is not None else ("python -m compileall -q ." if any(root.rglob("*.py")) else "git diff --check")
        if not isinstance(command, str) or not command.strip() or len(command) > 500: raise ValueError("command must be non-empty bounded text")
        return self._run(command, context)
