"""Prompt-toolkit based interactive chat surface."""
from __future__ import annotations

from typing import Callable
from .interactive_service import _human_result


def run_prompt_ui(session, *, mode: Callable[[], str]) -> None:
    """Run a fixed-footer multiline prompt with output-safe repainting."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.completion import Completer, Completion

    slash_commands = ("/help", "/status", "/model", "/plan", "/mode", "/connect", "/login", "/rules", "/files", "/skills", "/tree", "/review", "/test", "/compact", "/undo", "/pause", "/resume", "/cancel", "/clear", "/quit")

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            word = document.text_before_cursor.split()[-1] if document.text_before_cursor.split() else ""
            if not word.startswith("/"):
                return
            for command in slash_commands:
                if command.startswith(word):
                    yield Completion(command, start_position=-len(word), display=command)

    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event) -> None:
        # Enter submits, matching Codex-style chat.
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _(event) -> None:
        # Most terminals encode Shift+Enter as the Escape+Enter sequence.
        event.current_buffer.insert_text("\n")

    style = Style.from_dict({
        "prompt": "bg:#202123 #f5f5f5 bold",
        "continuation": "bg:#202123 #f5f5f5",
        "bottom-toolbar": "bg:#202123 #f5f5f5",
    })
    prompt_session = PromptSession(multiline=True, key_bindings=bindings, style=style, completer=SlashCompleter(), complete_while_typing=True)

    def prompt() -> HTML:
        return HTML(f"<prompt>╭─ forgecode │ {mode()}\n╰─❯ </prompt>")

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
            value = session.dispatch(text)
            if value is not None:
                rendered = _human_result(value)
                if rendered:
                    session.output(rendered)
