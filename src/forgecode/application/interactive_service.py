"""A deterministic, injectable REPL dispatcher.

The service deliberately does not own a second agent loop.  ``run_message``
is injected by the CLI and invokes the same production AgentLoop used by
headless runs.  Slash commands are handled locally and never sent to a
provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import difflib
import re
import shlex
import threading
import time
from typing import Callable, Iterable, TextIO


SHORTCUT_MAX_COMMAND_CHARS = 4_000


def _pretty_text(value: object) -> str:
    """Render model prose for the human REPL without exposing Markdown noise."""
    text = str(value).replace("**", "").replace("__", "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def _human_result(value: object) -> str | None:
    if not isinstance(value, dict):
        return _pretty_text(value)
    if value.get("accepted") is True and value.get("message") == "run started":
        return None
    if value.get("connected") is True:
        return f"Connected: {value.get('model') or 'model'} @ {value.get('base_url') or 'endpoint'}"
    if value.get("model_status") is True:
        return f"Model: {value.get('model') or 'not configured'}\nProvider: {value.get('provider') or 'unknown'}\nEndpoint: {value.get('base_url') or 'not configured'}\nProfile: {value.get('profile') or 'default'}"
    if "run_id" in value and "mode" in value and "worker" in value:
        worker = value.get("worker") if isinstance(value.get("worker"), dict) else {}
        verification = value.get("latest_verification")
        verification_text = "passed" if isinstance(verification, dict) and verification.get("ok") is True else ("failed" if isinstance(verification, dict) and verification.get("ok") is False else "not run")
        return "Status\n──────\n" + "\n".join((
            f"mode: {value.get('mode')}",
            f"run: {value.get('run_id') or '<new>'}",
            f"last state: {value.get('last_state') or 'idle'}",
            f"transactions: {value.get('transactions', 0)}",
            f"verification: {verification_text}",
            f"worker: {'running' if worker.get('active') else 'idle'} (queued: {worker.get('queue_items', 0)})",
            *( [f"elapsed: {_format_duration(worker['elapsed_seconds'])}"] if isinstance(worker.get('elapsed_seconds'), (int, float)) else [] ),
            *( [f"phase: {worker['phase']} · tools: {worker.get('tool_steps', 0)}"] if worker.get('phase') else [] ),
        ))
    if value.get("tools_status") is True:
        rows = value.get("tools") or []
        lines = ["Available tools", "───────────────"]
        groups: dict[str, list[object]] = {"Read-only": [], "Changes": [], "Execution": [], "Evidence": [], "Other": []}
        evidence_names = {"review", "test", "diagnostics", "git_status", "git_diff", "transaction", "rollback", "eval"}
        execution_names = {"run_command", "run_background", "process_status", "poll_process", "kill_process"}
        change_names = {"write_file", "apply_patch", "git_commit"}
        for row in rows:
            if not isinstance(row, dict):
                groups["Other"].append(row)
                continue
            name = str(row.get("name", "unknown"))
            if name in evidence_names:
                group = "Evidence"
            elif name in execution_names:
                group = "Execution"
            elif name in change_names or row.get("side_effecting"):
                group = "Changes"
            else:
                group = "Read-only"
            groups[group].append(row)
        for group, members in groups.items():
            if not members:
                continue
            lines.extend(("", group))
            for row in members:
                if isinstance(row, dict):
                    state = "✓" if row.get("available", True) else "—"
                    lines.append(f"  {state} {row.get('name', 'unknown')}: {row.get('description', '')}")
                else:
                    lines.append(f"  ✓ {row}")
        return "\n".join(lines)
    if "nodes" in value and "roots" in value and "edges" in value:
        nodes = value.get("nodes") or []
        lines = ["Session tree", "────────────", f"sessions: {len(nodes)}", f"roots: {len(value.get('roots') or [])}"]
        if not nodes:
            lines.append("No session branches yet")
        else:
            lines.append("")
            for node in nodes:
                if isinstance(node, dict):
                    lines.append(f"  • {node.get('run_id', '?')}  {node.get('state', 'unknown')}  ({node.get('events', 0)} events)")
        return "\n".join(lines)
    if value.get("diff_status") is True:
        diff = str(value.get("diff") or "")
        return "Git diff\n────────\n" + (diff[:20_000] if diff else "Working tree is clean")
    if "rollback_available" in value and "transaction_id" in value:
        lines = ["Review", "──────", f"transaction: {value.get('transaction_id')}", f"state: {value.get('state', 'unknown')}", f"rollback: {'available' if value.get('rollback_available') else 'unavailable'}"]
        issues = value.get("rollback_conflicts") or value.get("store_issues") or []
        if issues:
            lines.append("Issues:")
            lines.extend(f"  ⚠ {item}" for item in issues)
        return "\n".join(lines)
    if "summary" in value and ("before_chars" in value or "after_chars" in value):
        return "Context compacted\n─────────────────\n" + "\n".join((
            f"before: {value.get('before_chars', 0)} chars",
            f"after: {value.get('after_chars', 0)} chars",
            f"omitted messages: {value.get('omitted_messages', 0)}",
            "",
            str(value.get("summary") or "completed"),
        ))
    if "results" in value and "prefix" in value and value.get("advisory") is True:
        results = value.get("results") or []
        lines = ["Files", "─────", f"prefix: {value.get('prefix') or '<all>'}", f"matches: {len(results)}"]
        if results:
            lines.append("")
            lines.extend(f"  • {item}" for item in results)
        else:
            lines.append("No matching files")
        return "\n".join(lines)
    if "skills" in value and "errors" in value:
        skills = value.get("skills") or []
        errors = value.get("errors") or []
        lines = ["Skills", "──────", f"discovered: {len(skills)}"]
        if skills:
            lines.append("")
            for item in skills:
                if not isinstance(item, dict):
                    continue
                manifest = item.get("manifest") if isinstance(item.get("manifest"), dict) else {}
                skill_id = manifest.get("id", "unknown")
                name = manifest.get("name", skill_id)
                kind = manifest.get("entry_type", "markdown")
                state = "enabled" if manifest.get("enabled", True) else "disabled"
                lines.append(f"  • {skill_id} — {name} [{kind}, {state}]")
                description = str(manifest.get("description") or "").strip()
                if description:
                    lines.append(f"    {description}")
        if errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"  ✗ {error}" for error in errors)
        return "\n".join(lines)
    # ``/rules`` returns the RuleSet payload directly.  Keep this separate
    # from the generic dictionary fallback so the interactive TTY makes the
    # discovered sources and diagnostics visible (machine JSON is unchanged).
    if "sources" in value and "diagnostics" in value and "fingerprint" in value:
        sources = value.get("sources") or []
        diagnostics = value.get("diagnostics") or []
        lines = ["Rules", "─────", f"status: {'error' if any(isinstance(item, dict) and item.get('severity') == 'error' for item in diagnostics) else ('active' if sources else 'none')}", f"sources: {len(sources)}", f"characters: {value.get('chars', 0)}"]
        fingerprint = str(value.get("fingerprint") or "")
        if fingerprint:
            lines.append(f"fingerprint: {fingerprint[:16]}")
        if sources:
            lines.append("")
            lines.append("Sources:")
            for source in sources:
                if isinstance(source, dict):
                    path = source.get("path", "<unknown>")
                    scope = source.get("scope", ".")
                    suffix = " (truncated)" if source.get("truncated") else ""
                    lines.append(f"  • {path} [scope: {scope}]{suffix}")
        lines.append("")
        if diagnostics:
            lines.append("Diagnostics:")
            for item in diagnostics:
                if isinstance(item, dict):
                    severity = item.get("severity", "warning")
                    message = item.get("message", item.get("code", "unknown"))
                    path = f" ({item['path']})" if item.get("path") else ""
                    lines.append(f"  {'✗' if severity == 'error' else '⚠'} {message}{path}")
        else:
            lines.append("Diagnostics: none")
        return "\n".join(lines)
    if value.get("message") and value.get("state") == "completed":
        duration = value.get("duration_seconds")
        metrics = []
        if isinstance(duration, (int, float)):
            metrics.append(f"Worked for {_format_duration(duration)}")
        if isinstance(value.get("tool_steps"), int):
            metrics.append(f"{value['tool_steps']} tool steps")
        verification = value.get("verification_ok")
        status = "✓ Verification passed" if verification is True else ("✗ Verification failed" if verification is False else "• Verification not configured")
        summary = "Completed\n─────────\n" + status
        if metrics:
            summary += "\n" + " · ".join(metrics)
        changed = value.get("changed_files")
        if isinstance(changed, list) and changed:
            summary += "\nFiles changed: " + ", ".join(str(path) for path in changed[:20])
        return summary + "\n\n" + _pretty_text(value["message"])
    if value.get("error"):
        return f"Error: {_pretty_text(value.get('error'))}"
    if value.get("stopped") is True or value.get("cleared") is True:
        return None
    return None


def _format_duration(seconds: object) -> str:
    try:
        total = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "0s"
    if total < 60:
        return f"{total:.1f}s"
    minutes, remainder = divmod(int(total), 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {remainder}s"


@dataclass(frozen=True)
class CommandShortcut:
    """A validated terminal command shortcut.

    ``model`` (``!``) feeds the bounded command result into the next
    provider-neutral turn; ``local`` (``!!``) is user/audit-only.  The raw
    command is retained only in memory until the existing ShellTool executes
    it and is never part of the shortcut event schema.
    """

    kind: str
    command: str

    @property
    def prefix(self) -> str:
        return "!!" if self.kind == "local" else "!"


class ShortcutParseError(ValueError):
    """Input looked like a shortcut but violated its bounded grammar."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_command_shortcut(line: str) -> CommandShortcut | None:
    """Parse only an input-line prefix; ordinary prose containing ``!`` wins.

    The parser deliberately accepts no leading whitespace, no multiline input,
    and no ``!!!`` ambiguity.  This keeps the command boundary deterministic
    for both REPL and scripted transports.
    """

    if not isinstance(line, str):
        raise TypeError("interactive input must be text")
    if not line.startswith("!"):
        return None
    if "\n" in line or "\r" in line:
        raise ShortcutParseError("shortcut_multiline", "command shortcut must be a single input line")
    if line.startswith("!!!"):
        raise ShortcutParseError("shortcut_prefix", "command shortcut prefix must be ! or !!")
    prefix = "!!" if line.startswith("!!") else "!"
    command = line[len(prefix):].strip()
    if not command:
        raise ShortcutParseError("shortcut_empty", f"{prefix} command must not be empty")
    if len(command) > SHORTCUT_MAX_COMMAND_CHARS:
        raise ShortcutParseError("shortcut_too_long", f"command shortcut exceeds the {SHORTCUT_MAX_COMMAND_CHARS}-character safety limit")
    return CommandShortcut("local" if prefix == "!!" else "model", command)


class SlashCommandError(ValueError):
    pass


def _interactive_success(value: object) -> bool:
    """Classify a result for the machine envelope without hiding failures."""
    if not isinstance(value, dict):
        return True
    if value.get("error") or value.get("recovery_required") or value.get("unresolved"):
        return False
    if value.get("cancelled") is True or value.get("ok") is False:
        return False
    if "succeeded" in value and value.get("succeeded") is not True:
        return False
    if value.get("state") == "recovery_required":
        return False
    return True


def _interactive_error_code(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("code") or value.get("error") or value.get("stopped_reason") or value.get("state") or "interactive_failed")[:128]
    return "interactive_failed"


@dataclass
class InteractiveRunController:
    """Run one injected task engine while accepting bounded follow-ups.

    The controller owns no agent logic: ``start`` is the production
    ``run_message`` callback, so provider/tool/safety behavior remains in the
    single ``AgentLoop``. A daemon worker drains FIFO follow-ups and exposes
    thread-safe steering methods for terminal commands.
    """

    start: Callable[[str], object]
    on_result: Callable[[object], None] = lambda _value: None
    event_sink: Callable[[str, dict[str, object]], None] = lambda _kind, _payload: None
    pause_active: Callable[[], object] = lambda: {"paused": False, "error": "no active worker"}
    resume_active: Callable[[], object] = lambda: {"resumed": False, "error": "no active worker"}
    cancel_active: Callable[[], object] = lambda: {"cancelled": False, "error": "no active worker"}
    max_queue_items: int = 32
    max_queue_chars: int = 32_000
    _queue: list[str] = field(default_factory=list, init=False, repr=False)
    _queue_chars: int = field(default=0, init=False, repr=False)
    _active: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)
    _cancel_requested: bool = field(default=False, init=False, repr=False)
    _pending_pause: bool = field(default=False, init=False, repr=False)
    _pending_cancel: bool = field(default=False, init=False, repr=False)
    _started_monotonic: float | None = field(default=None, init=False, repr=False)
    _tool_steps: int = field(default=0, init=False, repr=False)
    _phase: str = field(default="", init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _condition: threading.Condition = field(default_factory=threading.Condition, init=False, repr=False)

    @property
    def active(self) -> bool:
        with self._condition:
            return self._active

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            elapsed = None if self._started_monotonic is None else max(0.0, time.monotonic() - self._started_monotonic)
            return {
                "active": self._active,
                "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
                "tool_steps": self._tool_steps,
                "phase": self._phase or None,
                "queue_items": len(self._queue),
                "queue_chars": self._queue_chars,
                "stopped": self._stopped,
                "cancellation_requested": self._cancel_requested,
                "pause_requested": self._pending_pause,
            }

    def submit(self, message: str) -> dict[str, object]:
        # Preserve leading whitespace: `` !cmd`` is ordinary prose, whereas
        # only a literal line-prefix ``!``/``!!`` is a shortcut.  Trim line
        # terminators but use ``strip`` solely for the empty-input check.
        text = str(message).rstrip("\r\n")
        if not text.strip():
            return {"accepted": False, "error": "message must not be empty"}
        with self._condition:
            if self._stopped:
                return {"accepted": False, "error": "interactive session is stopped"}
            if self._active:
                if self._cancel_requested:
                    self.event_sink("followup_rejected", {"reason": "cancellation_requested", "items": len(self._queue), "chars": self._queue_chars})
                    return {"accepted": False, "queued": False, "error": "run cancellation is already requested"}
                if len(self._queue) >= self.max_queue_items or self._queue_chars + len(text) > self.max_queue_chars:
                    self.event_sink("followup_rejected", {"reason": "queue_full", "items": len(self._queue), "chars": self._queue_chars})
                    return {"accepted": False, "queued": False, "error": "follow-up queue is full"}
                self._queue.append(text)
                self._queue_chars += len(text)
                self.event_sink("followup_enqueued", {"position": len(self._queue), "chars": len(text), "queue_items": len(self._queue)})
                self._condition.notify_all()
                return {"accepted": True, "queued": True, "position": len(self._queue)}
            self._active = True
            self._cancel_requested = False
            self._pending_pause = False
            self._pending_cancel = False
            self._started_monotonic = time.monotonic()
            self._tool_steps = 0
            self._phase = ""
            self.event_sink("run_enqueued", {"chars": len(text), "queue_items": 0})
            self._thread = threading.Thread(target=self._worker, args=(text,), name="forgecode-interactive", daemon=True)
            self._thread.start()
            return {"accepted": True, "queued": False, "message": "run started"}

    def _worker(self, first: str) -> None:
        message = first
        while True:
            value: object
            try:
                self.event_sink("run_dequeued", {"chars": len(message)})
                value = self.start(message)
            except BaseException as exc:
                value = {"error": f"interactive worker failed: {type(exc).__name__}: {exc}"}
                self.event_sink("run_failed", {"error": type(exc).__name__, "detail": str(exc)[:200]})
            try:
                self.on_result(value)
            except BaseException as exc:
                # Rendering is outside the execution boundary.  A closed
                # stdout must not make the worker report a second fake run.
                self.event_sink("result_output_failed", {"error": type(exc).__name__})
            self.event_sink(
                "run_finished",
                {
                    "ok": not (isinstance(value, dict) and bool(value.get("error"))),
                    "stopped_reason": value.get("stopped_reason") if isinstance(value, dict) else None,
                    "state": value.get("state") if isinstance(value, dict) else None,
                },
            )
            with self._condition:
                # Cancellation is fail-closed: follow-ups accepted before the
                # cancellation request must never start after the current run
                # returns.  A new submission is allowed only after this worker
                # has become inactive and explicitly resets the flag.
                if self._stopped or self._cancel_requested or not self._queue:
                    self._queue.clear()
                    self._queue_chars = 0
                    self._active = False
                    self._started_monotonic = None
                    self._tool_steps = 0
                    self._phase = ""
                    if not self._stopped:
                        self._cancel_requested = False
                    self._condition.notify_all()
                    return
                message = self._queue.pop(0)
                self._queue_chars -= len(message)
                self.event_sink("followup_dequeued", {"remaining": len(self._queue), "chars": len(message)})

    def _is_no_active_worker(self, value: object) -> bool:
        return isinstance(value, dict) and value.get("error") == "no active worker"

    def flush_pending_controls(self) -> tuple[object, ...]:
        """Apply controls received while the worker was still initializing.

        ``submit`` marks the worker active before its thread can construct a
        ``RunService``.  A terminal command can therefore race that short
        window; retaining the intent here prevents pause/cancel from being
        silently lost.
        """
        results: list[object] = []
        with self._condition:
            pending_cancel = self._pending_cancel
            pending_pause = self._pending_pause and not pending_cancel
            self._pending_cancel = False
            self._pending_pause = False
        if pending_cancel:
            results.append(self.cancel_active())
        elif pending_pause:
            results.append(self.pause_active())
        return tuple(results)

    def pause(self) -> object:
        if not self.active:
            return {"paused": False, "error": "no active worker"}
        result = self.pause_active()
        if self._is_no_active_worker(result):
            with self._condition:
                if self._active and not self._cancel_requested:
                    self._pending_pause = True
            self.event_sink("pause_pending", {"reason": "worker_initializing"})
            return {"paused": True, "pending": True, "message": "pause will apply at the next safe boundary"}
        return result

    def resume(self) -> object:
        if not self.active:
            return {"resumed": False, "error": "no active worker"}
        with self._condition:
            if self._pending_pause:
                self._pending_pause = False
                self.event_sink("resume_pending_cleared", {"reason": "worker_initializing"})
                return {"resumed": True, "pending": False, "message": "pending pause cleared before worker initialization"}
        return self.resume_active()

    def cancel(self) -> object:
        if not self.active:
            return {"cancelled": False, "error": "no active worker"}
        with self._condition:
            self._cancel_requested = True
            self._queue.clear()
            self._queue_chars = 0
        result = self.cancel_active()
        if self._is_no_active_worker(result):
            with self._condition:
                self._pending_cancel = True
            self.event_sink("cancel_pending", {"reason": "worker_initializing"})
            return {"cancelled": True, "pending": True, "message": "cancel will apply when the worker is initialized"}
        return result

    def stop(self, *, cancel: bool = True, timeout: float = 2.0) -> bool:
        with self._condition:
            self._stopped = True
            if cancel:
                self._cancel_requested = True
            self._queue.clear()
            self._queue_chars = 0
            self._condition.notify_all()
        if cancel:
            result = self.cancel_active()
            if self._is_no_active_worker(result):
                with self._condition:
                    # The worker may still be assembling its RunService.
                    # Preserve the cancellation intent so initialization can
                    # apply it before the first provider/tool boundary.
                    self._pending_cancel = True
                self.event_sink("cancel_pending", {"reason": "worker_initializing"})
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, min(float(timeout), 5.0)))
        stopped = not self.active
        if not stopped:
            self.event_sink("worker_unresolved", {"reason": "bounded_join_expired", "timeout_seconds": max(0.0, min(float(timeout), 5.0))})
        return stopped

    def join(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        return not self.active

    def update_metrics(self, *, tool_steps: int | None = None, phase: str | None = None) -> None:
        """Publish bounded live metrics from the injected production worker."""
        with self._condition:
            if tool_steps is not None:
                self._tool_steps = max(0, min(int(tool_steps), 100_000))
            if phase is not None:
                self._phase = str(phase)[:32]


@dataclass
class InteractiveSession:
    run_message: Callable[[str], object]
    status: Callable[[], object] = lambda: {}
    plan: Callable[[list[str]], object] = lambda _args: {}
    set_mode: Callable[[str], object] = lambda mode: {"mode": mode}
    review: Callable[[], object] = lambda: {}
    test: Callable[[list[str]], object] = lambda _args: {}
    compact: Callable[[], object] = lambda: {}
    undo: Callable[[list[str]], object] = lambda _args: {}
    rules: Callable[[], object] = lambda: {}
    tools: Callable[[], object] = lambda: {"tools_status": True, "tools": []}
    files: Callable[..., object] = lambda *_args: {}
    skills: Callable[[list[str]], object] = lambda _args: {}
    model: Callable[[list[str]], object] = lambda _args: {}
    connect: Callable[[list[str]], object] = lambda _args: {"error": "connect is unavailable"}
    login: Callable[[], object] = lambda: {"provider": "openai-compatible", "storage": "environment-only"}
    tree: Callable[[list[str]], object] = lambda _args: {}
    diff: Callable[[], object] = lambda: {}
    cancel: Callable[[], object] = lambda: {"cancelled": True}
    pause: Callable[[], object] = lambda: {"paused": True}
    resume: Callable[[], object] = lambda: {"resumed": True}
    quit: Callable[[], object] = lambda: {"stopped": True}
    output: Callable[[str], None] = print
    raw_output: Callable[[str], None] = lambda text: print(text, end="", flush=True)
    input_bar: Callable[[], None] = lambda: None
    clear_screen: Callable[[], object] = lambda: {"cleared": True}
    approval_pending: Callable[[], bool] = lambda: False
    submit_approval: Callable[[str], None] = lambda _line: None
    json_mode: bool = False
    # ``json_mode`` is retained for the v0.0.7 event shape.  ``jsonl_mode``
    # opts into the v0.0.8 command-envelope contract while keeping the old
    # ``type``/``payload`` aliases additive for scripts that consume them.
    jsonl_mode: bool = False
    max_queue_items: int = 32
    max_queue_chars: int = 32_000
    _queue: list[str] = field(default_factory=list)
    _queue_chars: int = 0
    stopped: bool = False
    controller: InteractiveRunController | None = None

    COMMANDS = ("help", "status", "tools", "plan", "mode", "model", "connect", "login", "rules", "files", "skills", "skill", "tree", "diff", "review", "test", "compact", "undo", "cancel", "pause", "resume", "clear", "quit", "exit")

    def header(self, *, run_id: str = "", mode: str = "plan", profile: str = "default", rules_count: int = 0, budget: int = 60_000) -> str:
        return f"ForgeCode session run={run_id or '<new>'} workspace=. mode={mode} profile={profile} rules={rules_count} budget={budget}"

    def help_text(self) -> str:
        return "/help /status /tools /model /plan [show|refresh] /mode plan|act|bypass /connect [provider] /login (alias) /rules /files [prefix] /skills [id] /tree /diff /review /test [command] /compact /undo [id|latest] /pause /resume /cancel /clear /quit (/exit); !<command> sends a bounded result to the model; !!<command> stays local"

    def dispatch(self, line: str) -> object | None:
        line = line.rstrip("\r\n")
        if not line.strip():
            return None
        try:
            shortcut = parse_command_shortcut(line)
        except ShortcutParseError as exc:
            return {"accepted": False, "shortcut": True, "error": str(exc), "code": exc.code}
        if shortcut is not None:
            # Keep execution on the controller's single worker so a shortcut
            # cannot race an active AgentLoop or bypass its FIFO queue.
            return self.controller.submit(line) if self.controller is not None else self.run_message(line)
        if not line.lstrip().startswith("/"):
            return self.controller.submit(line) if self.controller is not None else self.run_message(line)
        try:
            parts = shlex.split(line.strip(), posix=True)
        except ValueError as exc:
            raise SlashCommandError(f"invalid command syntax: {exc}") from exc
        command = parts[0][1:].lower()
        args = parts[1:]
        if command not in self.COMMANDS:
            # Keep malformed slash commands inside the chat loop, while making
            # typos recoverable and discoverable instead of forcing users to
            # remember the complete command vocabulary.
            matches = difflib.get_close_matches(command, self.COMMANDS, n=1, cutoff=0.55)
            hint = f"; did you mean /{matches[0]}?" if matches else "; use /help"
            raise SlashCommandError(f"unknown command /{command}{hint}")
        if command == "help":
            if args: raise SlashCommandError("usage: /help")
            return self.help_text()
        if command == "status":
            if args: raise SlashCommandError("usage: /status")
            return self.status()
        if command == "tools":
            if args: raise SlashCommandError("usage: /tools")
            return self.tools()
        if command == "plan":
            if len(args) > 1 or (args and args[0] not in {"show", "refresh"}): raise SlashCommandError("usage: /plan [show|refresh]")
            return self.plan(args)
        if command == "mode":
            if len(args) != 1 or args[0] not in {"plan", "act", "bypass"}:
                raise SlashCommandError("usage: /mode plan|act|bypass")
            return self.set_mode(args[0])
        if command == "model":
            if args and args[0] not in {"show", "list", "select"}:
                raise SlashCommandError("usage: /model [show|list|select <name>]")
            return self.model(args)
        if command == "connect":
            if len(args) > 1:
                raise SlashCommandError("usage: /connect [provider]")
            return self.connect(args)
        if command == "login":
            if args:
                raise SlashCommandError("usage: /login (alias for /connect)")
            return self.connect([])
        if command == "rules":
            if args: raise SlashCommandError("usage: /rules")
            return self.rules()
        if command == "files":
            if len(args) > 1: raise SlashCommandError("usage: /files [prefix]")
            return self.files(args[0]) if args else self.files()
        if command in {"skills", "skill"}:
            if len(args) > 2 or (len(args) == 2 and args[1] != "--approve"): raise SlashCommandError("usage: /skills [id] [--approve]")
            return self.skills(args)
        if command == "review":
            if args: raise SlashCommandError("usage: /review")
            return self.review()
        if command == "tree":
            if args: raise SlashCommandError("usage: /tree")
            return self.tree([])
        if command == "diff":
            if args: raise SlashCommandError("usage: /diff")
            return self.diff()
        if command == "test": return self.test(args)
        if command == "compact":
            if args: raise SlashCommandError("usage: /compact")
            return self.compact()
        if command == "undo":
            if len(args) > 1: raise SlashCommandError("usage: /undo [id|latest]")
            return self.undo(args or ["latest"])
        if command == "cancel":
            if args: raise SlashCommandError("usage: /cancel")
            return self.cancel()
        if command == "pause":
            if args: raise SlashCommandError("usage: /pause")
            return self.pause()
        if command == "resume":
            if args: raise SlashCommandError("usage: /resume")
            return self.resume()
        if command == "clear":
            if args: raise SlashCommandError("usage: /clear")
            return self.clear_screen()
        if command in {"quit", "exit"}:
            if args: raise SlashCommandError(f"usage: /{command}")
            result = self.quit()
            self.stopped = True
            return result
        raise SlashCommandError("unreachable command")

    def enqueue(self, message: str) -> None:
        if len(self._queue) >= self.max_queue_items or self._queue_chars + len(message) > self.max_queue_chars:
            raise SlashCommandError("follow-up queue is full")
        self._queue.append(message)
        self._queue_chars += len(message)

    def drain(self) -> list[object]:
        results: list[object] = []
        while self._queue and not self.stopped:
            message = self._queue.pop(0)
            self._queue_chars -= len(message)
            results.append(self.run_message(message))
        return results

    def run_stream(self, stream: Iterable[str]) -> list[object]:
        results: list[object] = []
        iterator = iter(stream)
        is_tty = bool(getattr(stream, "isatty", lambda: False)()) and not (self.json_mode or self.jsonl_mode)
        while True:
            if is_tty:
                self.input_bar()
            try:
                line = next(iterator)
            except StopIteration:
                break
            if self.stopped:
                break
            # A background agent must never read stdin directly. Route the
            # next terminal line to its pending approval request instead of
            # treating `y`/`n` as a new model prompt.
            if self.approval_pending():
                self.submit_approval(line.rstrip("\r\n"))
                continue
            # Terminals commonly deliver Escape as a standalone control byte
            # (``\x1b``). Treat it as an immediate cancellation request rather
            # than waiting for a slash command or a completed prompt.
            if "\x1b" in line:
                value = self.cancel()
                if self.jsonl_mode:
                    record = {"schema_version": 1, "kind": "interactive_result", "ok": True, "command": "chat", "data": value, "type": "interactive_result", "payload": value}
                    self.output(json.dumps(record, ensure_ascii=False, default=str, allow_nan=False))
                elif value is not None:
                    results.append(value)
                    if self.json_mode:
                        self.output(json.dumps({"type": "interactive_result", "payload": value}, ensure_ascii=False, default=str))
                    else:
                        rendered = _human_result(value)
                        if rendered:
                            self.output(rendered)
                continue
            # Preserve the historical sequential semantics for stateful slash
            # commands while leaving control commands responsive during an
            # active run.  A bounded wait prevents EOF/REPL shutdown from
            # hanging forever on a provider that will not return.
            if self.controller is not None and line.lstrip().startswith("/"):
                command_hint = line.lstrip()[1:].split(None, 1)[0].lower() if line.lstrip()[1:] else ""
                if command_hint not in {"pause", "resume", "cancel", "status", "help", "quit", "model"} and self.controller.active:
                    self.controller.join(30.0)
            try:
                value = self.dispatch(line)
            except SlashCommandError as exc:
                value = {"error": str(exc)}
            if self.controller is not None and self.json_mode and not line.lstrip().startswith("/") and self.controller.active:
                # ``--json`` is the legacy scripted transport and historically
                # exposed one completed result per input line.  Preserve that
                # ordering for existing scripts; ``--jsonl`` remains the
                # asynchronous, controller-oriented transport.
                self.controller.join(30.0)
            if value is not None:
                results.append(value)
                if self.jsonl_mode:
                    success = _interactive_success(value)
                    if success:
                        record = {"schema_version": 1, "kind": "interactive_result", "ok": True, "command": "chat", "data": value, "type": "interactive_result", "payload": value}
                    else:
                        message = str(value.get("message") or value.get("error") or "interactive command failed") if isinstance(value, dict) else str(value)
                        code = _interactive_error_code(value)
                        record = {"schema_version": 1, "kind": "error", "ok": False, "command": "chat", "error": {"code": code[:128], "message": message[:2_000]}, "type": "interactive_result", "payload": value}
                    self.output(json.dumps(record, ensure_ascii=False, default=str, allow_nan=False))
                else:
                    if self.json_mode:
                        self.output(json.dumps({"type": "interactive_result", "payload": value}, ensure_ascii=False, default=str))
                    else:
                        rendered = _human_result(value)
                        if rendered:
                            self.output(rendered)
                if is_tty:
                    self.input_bar()
            if self.stopped:
                break
        return results


__all__ = [
    "CommandShortcut",
    "InteractiveRunController",
    "InteractiveSession",
    "SHORTCUT_MAX_COMMAND_CHARS",
    "ShortcutParseError",
    "SlashCommandError",
    "parse_command_shortcut",
]
