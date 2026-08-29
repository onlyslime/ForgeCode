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
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.styles import Style

    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event) -> None:
        # Enter submits, matching Codex-style chat. Shift+Enter below is the
        # explicit multiline insertion gesture.
        event.current_buffer.validate_and_handle()

    @bindings.add("s-enter")
    def _(event) -> None:
        event.current_buffer.insert_text("\n")

    style = Style.from_dict({
        "prompt": "bg:#202123 #f5f5f5 bold",
        "bottom-toolbar": "bg:#202123 #f5f5f5",
    })
    prompt_session = PromptSession(multiline=True, key_bindings=bindings, style=style)

    def prompt() -> HTML:
        return HTML(f"<prompt>╭─ forgecode │ {mode()}\n╰─❯ </prompt>")

    with patch_stdout(raw=True):
        while not session.stopped:
            try:
                text = prompt_session.prompt(prompt)
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
