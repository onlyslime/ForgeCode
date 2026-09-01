"""Prompt-toolkit based interactive chat surface."""
from __future__ import annotations

from typing import Callable
import os
from .interactive_service import SlashCommandError, _human_result


def _vivid(text: str, *, enabled: bool | None = None) -> str:
    """Color human-only output; JSON/JSONL paths never call this renderer."""
    if enabled is None:
        enabled = not os.environ.get("NO_COLOR") and os.environ.get("FORGECODE_THEME", "vivid").lower() not in {"none", "minimal"}
    if not enabled:
        return text
    colors = {"blue": "\x1b[94m", "cyan": "\x1b[36m", "green": "\x1b[32m", "yellow": "\x1b[33m", "red": "\x1b[31m", "magenta": "\x1b[35m", "dim": "\x1b[2m"}
    reset = "\x1b[0m"
    headings = {"Status", "Review", "Rules", "Skills", "Files", "Recent events", "Context index", "Session tree", "Git diff", "Queue", "Available tools", "Completed", "Context compacted"}
    result = []
    for line in text.splitlines():
        s = line.strip()
        color = ""
        if s in headings or set(s) <= {"─", "━", "═"}:
            color = colors["blue"]
        elif s.startswith(("✓", "Verification passed", "Index is healthy")):
            color = colors["green"]
        elif s.startswith(("✗", "Error", "failed", "Failures")):
            color = colors["red"]
        elif s.startswith(("⚠", "Approval", "awaiting", "Issues:")):
            color = colors["yellow"]
        elif s.startswith(("•", "phase:", "Model:", "Provider:")):
            color = colors["cyan"]
        elif s.startswith(("transaction:", "rollback:", "audit:")):
            color = colors["magenta"]
        if color:
            line = f"{color}{line}{reset}"
        result.append(line)
    return "\n".join(result)


def run_prompt_ui(session, *, mode: Callable[[], str]) -> None:
    """Run a fixed-footer multiline prompt with output-safe repainting."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.application.current import get_app
    from html import escape

    slash_commands = ("/help", "/introduce", "/status", "/queue", "/steer", "/tools", "/model", "/plan", "/mode", "/connect", "/login", "/rules", "/files", "/skills", "/tree", "/diff", "/context", "/events", "/memory", "/review", "/test", "/compact", "/undo", "/pause", "/resume", "/cancel", "/clear", "/quit", "/exit")
    argument_choices = {
        "/mode": ("plan", "act", "bypass"),
        "/plan": ("show", "refresh"),
        "/model": ("show", "list", "select"),
        "/undo": ("latest",),
        "/events": ("run_created", "model_request", "provider_retry", "tool_call", "tool_result", "error", "verification_result"),
        "/files": (),
        "/skills": ("list", "check", "show"),
        "/tree": (),
        "/test": ("list", "show", "run"),
    }

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            before = document.text_before_cursor
            words = before.split()
            if not words:
                return
            command = words[0].lower()
            if command in argument_choices and (len(words) > 1 or before.endswith((" ", "\t"))):
                prefix = words[-1]
                if before.endswith((" ", "\t")):
                    prefix = ""
                for choice in argument_choices[command]:
                    if choice.startswith(prefix):
                        yield Completion(choice, start_position=-len(prefix), display=choice)
                return
            word = words[-1]
            if not word.startswith("/"):
                return
            for candidate in slash_commands:
                if candidate.startswith(word):
                    yield Completion(candidate, start_position=-len(word), display=candidate)

    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event) -> None:
        # Enter submits, matching Codex-style chat.
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _(event) -> None:
        # Most terminals encode Shift+Enter as the Escape+Enter sequence.
        event.current_buffer.insert_text("\n")

    @bindings.add("escape")
    def _(event) -> None:
        # A standalone Esc cancels the active agent run without closing chat.
        # Keep the buffer intact so the user can edit or submit a follow-up.
        result = session.cancel()
        rendered = _human_result(result)
        if rendered:
            session.output(_vivid(rendered))
        event.app.invalidate()

    style = Style.from_dict({
        "prompt": "bg:#202123 #f5f5f5 bold",
        "continuation": "bg:#202123 #f5f5f5",
        "bottom-toolbar": "bg:#202123 #f5f5f5",
    })
    def notify_approval() -> None:
        try:
            get_app().invalidate()
        except Exception:
            pass

    session.approval_notify = notify_approval
    prompt_session = PromptSession(multiline=True, key_bindings=bindings, style=style, completer=SlashCompleter(), complete_while_typing=True)

    def prompt() -> HTML:
        approval = ""
        if session.approval_pending():
            text = escape(session.approval_prompt() or "Approval required: enter y or n")
            approval = f"<approval>⚠ {text}</approval>\n"
        return HTML(f"{approval}<prompt>╭─ forgecode │ {mode()}\n╰─❯ </prompt>")

    with patch_stdout(raw=True):
        while not session.stopped:
            try:
                text = prompt_session.prompt(prompt, prompt_continuation=lambda width, line, wrap_count: HTML("<continuation>│ </continuation>"))
            except (EOFError, KeyboardInterrupt):
                session.cancel()
                break
            if session.approval_pending():
                session.submit_approval(text.strip())
                continue
            try:
                value = session.dispatch(text)
            except SlashCommandError as exc:
                value = {"error": str(exc)}
            if value is not None:
                rendered = _human_result(value)
                if rendered:
                    session.output(rendered)
