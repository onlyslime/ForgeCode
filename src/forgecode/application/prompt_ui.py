"""Prompt-toolkit based interactive chat surface."""
from __future__ import annotations

from typing import Callable
from .interactive_service import _human_result


def run_prompt_ui(session, *, mode: Callable[[], str]) -> None:
    """Run a fixed-footer multiline prompt with output-safe repainting."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.formatted_text import HTML

    prompt_session = PromptSession(multiline=True)

    def prompt() -> HTML:
        return HTML(f"<ansiblack>╭─ forgecode │ {mode()}\n╰─❯ </ansiblack>")

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
