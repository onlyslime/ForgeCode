"""Controlled background process tools with incremental output polling."""
from __future__ import annotations
from dataclasses import dataclass, field
import os, subprocess, threading, time, uuid
from typing import Any
from .base import ToolContext, ToolDefinition, ToolResult
from .shell import classify_command

@dataclass
class _Process:
    process: subprocess.Popen
    command: str
    started: float
    output: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    truncated: bool = False
    output_chars: int = 0
    finished: float | None = None

class ProcessManager:
    def __init__(self, max_output_chars: int = 100_000, max_tasks: int = 64, max_history: int = 256):
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (max_output_chars, max_tasks, max_history)):
            raise ValueError("background limits must be positive integers")
        self.max_output_chars = max_output_chars
        self.max_tasks = max_tasks
        self.max_history = max_history
        self._items: dict[str, _Process] = {}
        self._lock = threading.Lock()

    def start(self, command: str, root, task_id: str) -> _Process:
        with self._lock:
            active = sum(item.process.poll() is None for item in self._items.values())
            if active >= self.max_tasks:
                raise RuntimeError("background task limit exceeded")
        process = subprocess.Popen(command, cwd=root, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        item = _Process(process, command, time.monotonic())
        with self._lock:
            self._items[task_id] = item
            if len(self._items) > self.max_history:
                finished = [key for key, value in self._items.items() if value.process.poll() is not None]
                for key in finished[: max(0, len(self._items) - self.max_history)]:
                    self._items.pop(key, None)
        def drain():
            assert process.stdout is not None
            for line in process.stdout:
                with item.lock:
                    clean = line.rstrip("\r\n")
                    remaining = self.max_output_chars - item.output_chars
                    if remaining > 0:
                        item.output.append(clean[:remaining])
                        item.output_chars += min(len(clean), remaining)
                    if len(clean) > remaining:
                        item.truncated = True
            process.wait()
            with item.lock: item.finished = time.monotonic()
        threading.Thread(target=drain, name=f"forgecode-bg-{task_id[:8]}", daemon=True).start()
        return item

    def get(self, task_id: str) -> _Process | None:
        with self._lock: return self._items.get(task_id)

    def snapshot(self, task_id: str, cursor: int = 0) -> dict[str, Any]:
        item = self.get(task_id)
        if item is None: return {"error": "unknown_task", "task_id": task_id}
        with item.lock: lines = item.output[cursor:]; total = len(item.output); truncated = item.truncated
        code = item.process.poll()
        ended = item.finished if item.finished is not None else time.monotonic()
        return {"task_id": task_id, "status": "running" if code is None else ("completed" if code == 0 else "failed"), "exit_code": code, "output": "\n".join(lines), "cursor": total, "truncated": truncated, "duration_seconds": round(ended - item.started, 3), "pid": item.process.pid}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._items)[:128]
        rows = []
        for task_id in ids:
            data = self.snapshot(task_id)
            data.pop("output", None)
            rows.append(data)
        return rows

class RunBackgroundTool:
    definition = ToolDefinition("run_background", "Start a bounded approved background command and return its task ID.", {"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}, side_effecting=True)
    def __init__(self, guard, manager): self.guard, self.manager = guard, manager
    def execute(self, arguments, context):
        denied = context.deny_if_plan(self.definition.name)
        if denied: return denied
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > 4_000: raise ValueError("command must be 1-4000 characters")
        risk, reasons, blocked = classify_command(command)
        if blocked: return ToolResult(False, "command blocked by safety policy", {"error":"risk_blocked", "risk":risk, "risk_reasons":list(reasons)})
        if not context.request_approval(self.definition.name, {"command":command, "_risk":risk, "_risk_reasons":list(reasons)}): return ToolResult(False, "run_background denied by approval policy", {"error":"approval_denied"})
        if context.cancelled: return ToolResult(False, "run_background cancelled before start", {"error":"cancelled"})
        task_id = uuid.uuid4().hex[:16]
        self.manager.start(command, context.guard.root, task_id)
        return ToolResult(True, f"background task started: {task_id}", {"task_id":task_id, "status":"running", "command":command})

class ProcessStatusTool:
    definition = ToolDefinition("process_status", "Get the status of a ForgeCode background task.", {"type":"object","properties":{"task_id":{"type":"string"}},"required":["task_id"]})
    def __init__(self, guard, manager): self.manager = manager
    def execute(self, arguments, context): return ToolResult(True, str(self.manager.snapshot(str(arguments.get("task_id", "")))), self.manager.snapshot(str(arguments.get("task_id", ""))))

class ListProcessesTool:
    definition = ToolDefinition("list_processes", "List bounded ForgeCode background task summaries.", {"type":"object"})
    def __init__(self, guard, manager): self.manager = manager
    def execute(self, arguments, context):
        rows = self.manager.list()
        return ToolResult(True, "\n".join(f"{r['task_id']} {r['status']} pid={r['pid']} duration={r['duration_seconds']}s" for r in rows) or "no background tasks", {"tasks": rows, "count": len(rows)})

class PollProcessTool(ProcessStatusTool):
    definition = ToolDefinition("poll_process", "Read new output from a background task using a cursor.", {"type":"object","properties":{"task_id":{"type":"string"},"cursor":{"type":"integer"}},"required":["task_id"]})
    def execute(self, arguments, context):
        cursor = arguments.get("cursor", 0)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0: raise ValueError("cursor must be a non-negative integer")
        data = self.manager.snapshot(str(arguments.get("task_id", "")), cursor)
        return ToolResult("error" not in data, data.get("output", "") or data.get("status", data.get("error", "unknown")), data)

class KillProcessTool(ProcessStatusTool):
    definition = ToolDefinition("kill_process", "Terminate a ForgeCode-owned background task after approval.", {"type":"object","properties":{"task_id":{"type":"string"}},"required":["task_id"]}, side_effecting=True)
    def execute(self, arguments, context):
        denied = context.deny_if_plan(self.definition.name)
        if denied: return denied
        task_id = str(arguments.get("task_id", "")); item = self.manager.get(task_id)
        if item is None: return ToolResult(False, "unknown background task", {"error":"unknown_task"})
        if not context.request_approval(self.definition.name, {"task_id":task_id}): return ToolResult(False, "kill_process denied by approval policy", {"error":"approval_denied"})
        if item.process.poll() is None: item.process.terminate()
        return ToolResult(True, f"background task stopped: {task_id}", {"task_id":task_id, "status":"cancelled"})
