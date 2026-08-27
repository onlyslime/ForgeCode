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
    files: Callable[[], object] = lambda: {}
    quit: Callable[[], object] = lambda: {"stopped": True}
    output: Callable[[str], None] = print
    json_mode: bool = False
    max_queue_items: int = 32
    max_queue_chars: int = 32_000
    _queue: list[str] = field(default_factory=list)
    _queue_chars: int = 0
    stopped: bool = False

    COMMANDS = ("help", "status", "plan", "mode", "rules", "files", "review", "test", "compact", "undo", "quit")

    def header(self, *, run_id: str = "", mode: str = "plan", profile: str = "default", rules_count: int = 0, budget: int = 60_000) -> str:
        return f"ForgeCode session run={run_id or '<new>'} workspace=. mode={mode} profile={profile} rules={rules_count} budget={budget}"

    def help_text(self) -> str:
        return "/help /status /plan [show|refresh] /mode plan|act /rules /files /review /test [command] /compact /undo [id|latest] /quit"

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
        if command == "rules":
            if args: raise SlashCommandError("usage: /rules")
            return self.rules()
        if command == "files":
            if args: raise SlashCommandError("usage: /files")
            return self.files()
        if command == "review":
            if args: raise SlashCommandError("usage: /review")
            return self.review()
        if command == "test": return self.test(args)
        if command == "compact":
            if args: raise SlashCommandError("usage: /compact")
            return self.compact()
        if command == "undo":
            if len(args) > 1: raise SlashCommandError("usage: /undo [id|latest]")
            return self.undo(args or ["latest"])
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
                self.output(json.dumps({"type": "interactive_result", "payload": value}, ensure_ascii=False, default=str) if self.json_mode else str(value))
            if self.stopped:
                break
        return results


__all__ = ["InteractiveSession", "SlashCommandError"]
