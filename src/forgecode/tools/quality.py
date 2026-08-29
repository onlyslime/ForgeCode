"""Bounded project quality checks exposed as first-class tools."""
from __future__ import annotations
import subprocess
from typing import Any
from .base import ToolContext, ToolDefinition, ToolResult
from .filesystem import ListFilesTool

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
        if not context.request_approval(self.definition.name, {"command": command}):
            return ToolResult(False, f"{self.definition.name} denied by approval policy", {"error": "approval_denied"})
        if context.cancelled:
            return ToolResult(False, f"{self.definition.name} cancelled before execution", {"error": "cancelled"})
        try:
            p = subprocess.run(command, cwd=context.guard.root, shell=True, capture_output=True, text=True, timeout=min(60.0, context.remaining_seconds(60.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"check failed: {type(exc).__name__}", {"error": "check_failed"})
        output = (p.stdout + ("\n" + p.stderr if p.stderr else "")).strip()[:20_000]
        return ToolResult(p.returncode == 0, output or ("passed" if p.returncode == 0 else "failed"), {"exit_code": p.returncode})

class TestTool(_CheckTool):
    definition = ToolDefinition("test", "Run the project's tests using its detected test runner.", {"type":"object","properties":{"command":{"type":"string"}},"additionalProperties":False}, side_effecting=True)
    def execute(self, arguments, context):
        command = arguments.get("command") or ("pytest -q" if (context.guard.root / "pytest.ini").exists() or (context.guard.root / "tests").is_dir() else "python -m unittest")
        if not isinstance(command, str) or len(command) > 500: raise ValueError("command must be bounded text")
        return self._run(command, context)

class DiagnosticsTool(_CheckTool):
    definition = ToolDefinition("diagnostics", "Run bounded compile/type diagnostics for the project.", {"type":"object","properties":{"command":{"type":"string"}},"additionalProperties":False}, side_effecting=True)
    def execute(self, arguments, context):
        root = context.guard.root
        command = arguments.get("command") or ("python -m compileall -q ." if any(root.rglob("*.py")) else "git diff --check")
        if not isinstance(command, str) or len(command) > 500: raise ValueError("command must be bounded text")
        return self._run(command, context)
