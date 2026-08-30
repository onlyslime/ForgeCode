"""Controlled background process tools with incremental output polling."""
from __future__ import annotations
from dataclasses import dataclass, field
import json, os, subprocess, tempfile, threading, time, uuid
from pathlib import Path
from typing import Any
from .base import ToolContext, ToolDefinition, ToolResult
from .shell import classify_command
from ..security.workspace import WorkspaceViolation, assert_no_path_alias

_MAX_STATE_BYTES = 2_000_000
_MAX_TASK_ID_CHARS = 128

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
    def __init__(self, max_output_chars: int = 100_000, max_tasks: int = 64, max_history: int = 256, state_path: Path | str | None = None):
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (max_output_chars, max_tasks, max_history)):
            raise ValueError("background limits must be positive integers")
        self.max_output_chars = max_output_chars
        self.max_tasks = max_tasks
        self.max_history = max_history
        self._items: dict[str, _Process] = {}
        self._stale: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        if state_path is None:
            self._state_path = None
        else:
            try:
                self._state_path = assert_no_path_alias(Path(state_path), message="background state path is a symlink or junction alias")
            except WorkspaceViolation as exc:
                raise ValueError(str(exc)) from exc
        if self._state_path is not None:
            self._load_state()

    def _load_state(self) -> None:
        try:
            assert_no_path_alias(self._state_path, message="background state path is a symlink or junction alias")
        except WorkspaceViolation:
            return
        try:
            if self._state_path.stat().st_size > _MAX_STATE_BYTES:
                return
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            rows = payload.get("tasks", []) if isinstance(payload, dict) else []
            if isinstance(rows, list):
                for row in rows[-self.max_history:]:
                    task_id = row.get("task_id") if isinstance(row, dict) else None
                    if isinstance(task_id, str) and 0 < len(task_id) <= _MAX_TASK_ID_CHARS and not any(ch in task_id for ch in "\r\n"):
                        status = "stale" if row.get("status") == "running" else str(row.get("status", "stale"))
                        if status not in {"stale", "completed", "failed", "cancelled"}:
                            status = "stale"
                        self._stale[task_id] = {"task_id": task_id, "status": status, "recoverable": False}
        except (OSError, ValueError, TypeError):
            return

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        try:
            assert_no_path_alias(self._state_path.parent, message="background state directory is a symlink or junction alias")
            assert_no_path_alias(self._state_path, message="background state path is a symlink or junction alias")
        except WorkspaceViolation:
            return
        rows = []
        for task_id, item in self._items.items():
            rows.append({"task_id": task_id, "pid": item.process.pid, "started": item.started, "status": "running" if item.process.poll() is None else "completed"})
        rows.extend(self._stale.values())
        rows = rows[-self.max_history:]
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix="background-", suffix=".tmp", dir=self._state_path.parent)
            tmp = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    descriptor = -1
                    stream.write(json.dumps({"schema_version": 1, "tasks": rows}, ensure_ascii=False))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self._state_path)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                tmp.unlink(missing_ok=True)
        except OSError:
            return

    def start(self, command: str, root, task_id: str) -> _Process:
        if not isinstance(task_id, str) or not 0 < len(task_id) <= _MAX_TASK_ID_CHARS or any(ch in task_id for ch in "\r\n"):
            raise ValueError("task_id must be bounded newline-safe text")
        with self._lock:
            active = sum(item.process.poll() is None for item in self._items.values())
            if active >= self.max_tasks:
                raise RuntimeError("background task limit exceeded")
            if task_id in self._items:
                raise RuntimeError("background task id already exists")
            # Keep the admission check and process registration under one
            # lock. Otherwise concurrent callers can both pass the limit and
            # temporarily exceed the configured task budget.
            process = subprocess.Popen(command, cwd=root, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            item = _Process(process, command, time.monotonic())
            self._items[task_id] = item
            self._stale.pop(task_id, None)
            self._persist_state()
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
            with self._lock: self._persist_state()
        threading.Thread(target=drain, name=f"forgecode-bg-{task_id[:8]}", daemon=True).start()
        return item

    def get(self, task_id: str) -> _Process | None:
        with self._lock: return self._items.get(task_id)

    def snapshot(self, task_id: str, cursor: int = 0) -> dict[str, Any]:
        item = self.get(task_id)
        if item is None:
            stale = self._stale.get(task_id)
            return {**stale, "task_id": task_id} if stale is not None else {"error": "unknown_task", "task_id": task_id}
        with item.lock: lines = item.output[cursor:]; total = len(item.output); truncated = item.truncated
        code = item.process.poll()
        ended = item.finished if item.finished is not None else time.monotonic()
        return {"task_id": task_id, "status": "running" if code is None else ("completed" if code == 0 else "failed"), "exit_code": code, "output": "\n".join(lines), "cursor": total, "truncated": truncated, "duration_seconds": round(ended - item.started, 3), "pid": item.process.pid, "recoverable": False}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._items)[:128] + [key for key in self._stale if key not in self._items][:128]
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
        return ToolResult(True, f"background task started: {task_id}", {"task_id":task_id, "status":"running"})

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
        if item.process.poll() is not None:
            return ToolResult(True, f"background task already exited: {task_id}", {"task_id": task_id, "status": "already_exited", "exit_code": item.process.returncode, "termination_result": "already_exited"})
        item.process.terminate()
        try:
            item.process.wait(timeout=min(0.5, context.remaining_seconds(0.5)))
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"background task termination unresolved: {task_id}", {"task_id": task_id, "status": "running", "termination_result": "unresolved", "error": "termination_unresolved", "pid": item.process.pid})
        return ToolResult(True, f"background task stopped: {task_id}", {"task_id": task_id, "status": "cancelled", "exit_code": item.process.returncode, "termination_result": "confirmed"})
