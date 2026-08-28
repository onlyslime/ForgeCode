"""A deterministic, injectable REPL dispatcher.

The service deliberately does not own a second agent loop.  ``run_message``
is injected by the CLI and invokes the same production AgentLoop used by
headless runs.  Slash commands are handled locally and never sent to a
provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import shlex
from typing import Callable, Iterable, TextIO


class SlashCommandError(ValueError):
    pass


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
    files: Callable[..., object] = lambda *_args: {}
    skills: Callable[[list[str]], object] = lambda _args: {}
    model: Callable[[list[str]], object] = lambda _args: {}
    tree: Callable[[list[str]], object] = lambda _args: {}
    cancel: Callable[[], object] = lambda: {"cancelled": True}
    pause: Callable[[], object] = lambda: {"paused": True}
    resume: Callable[[], object] = lambda: {"resumed": True}
    quit: Callable[[], object] = lambda: {"stopped": True}
    output: Callable[[str], None] = print
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

    COMMANDS = ("help", "status", "plan", "mode", "model", "rules", "files", "skills", "skill", "tree", "review", "test", "compact", "undo", "cancel", "pause", "resume", "quit")

    def header(self, *, run_id: str = "", mode: str = "plan", profile: str = "default", rules_count: int = 0, budget: int = 60_000) -> str:
        return f"ForgeCode session run={run_id or '<new>'} workspace=. mode={mode} profile={profile} rules={rules_count} budget={budget}"

    def help_text(self) -> str:
        return "/help /status /plan [show|refresh] /mode plan|act /model [list|show|select <name>] /rules /files [prefix] /skills [id] /tree /review /test [command] /compact /undo [id|latest] /pause /resume /cancel /quit"

    def dispatch(self, line: str) -> object | None:
        line = line.rstrip("\r\n")
        if not line.strip():
            return None
        if not line.lstrip().startswith("/"):
            return self.run_message(line)
        try:
            parts = shlex.split(line.strip(), posix=True)
        except ValueError as exc:
            raise SlashCommandError(f"invalid command syntax: {exc}") from exc
        command = parts[0][1:].lower()
        args = parts[1:]
        if command not in self.COMMANDS:
            raise SlashCommandError(f"unknown command /{command}; use /help")
        if command == "help":
            if args: raise SlashCommandError("usage: /help")
            return self.help_text()
        if command == "status":
            if args: raise SlashCommandError("usage: /status")
            return self.status()
        if command == "plan":
            if len(args) > 1 or (args and args[0] not in {"show", "refresh"}): raise SlashCommandError("usage: /plan [show|refresh]")
            return self.plan(args)
        if command == "mode":
            if len(args) != 1 or args[0] not in {"plan", "act"}:
                raise SlashCommandError("usage: /mode plan|act")
            return self.set_mode(args[0])
        if command == "model":
            if len(args) > 2 or (args and args[0] not in {"list", "show", "select"}):
                raise SlashCommandError("usage: /model [list|show|select <name>]")
            if args and args[0] == "select" and len(args) != 2:
                raise SlashCommandError("usage: /model select <name>")
            return self.model(args)
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
        if command == "quit":
            if args: raise SlashCommandError("usage: /quit")
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
        for line in stream:
            if self.stopped:
                break
            try:
                value = self.dispatch(line)
            except SlashCommandError as exc:
                value = {"error": str(exc)}
            if value is not None:
                results.append(value)
                if self.jsonl_mode:
                    success = not (isinstance(value, dict) and value.get("error"))
                    if success:
                        record = {"schema_version": 1, "kind": "interactive_result", "ok": True, "command": "chat", "data": value, "type": "interactive_result", "payload": value}
                    else:
                        message = str(value.get("message") or value.get("error") or "interactive command failed") if isinstance(value, dict) else str(value)
                        record = {"schema_version": 1, "kind": "error", "ok": False, "command": "chat", "error": {"code": str(value.get("error", "interactive_failed")) if isinstance(value, dict) else "interactive_failed", "message": message[:2_000]}, "type": "interactive_result", "payload": value}
                    self.output(json.dumps(record, ensure_ascii=False, default=str, allow_nan=False))
                else:
                    self.output(json.dumps({"type": "interactive_result", "payload": value}, ensure_ascii=False, default=str) if self.json_mode else str(value))
            if self.stopped:
                break
        return results


__all__ = ["InteractiveSession", "SlashCommandError"]
