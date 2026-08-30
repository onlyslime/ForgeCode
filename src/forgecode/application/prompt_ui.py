"""Full-screen prompt-toolkit terminal interface."""
from __future__ import annotations

import re
import threading
from contextlib import redirect_stdout
from typing import Callable

from .interactive_service import SlashCommandError, _human_result

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def run_prompt_ui(session, *, mode: Callable[[], str]) -> None:
    """Run ForgeCode in a full-screen TUI with a scrolling transcript."""
    from prompt_toolkit import Application
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import TextArea
    from prompt_toolkit.styles import Style

    commands = ("/help", "/status", "/queue", "/tools", "/model", "/plan", "/mode", "/connect", "/login", "/rules", "/files", "/skills", "/tree", "/diff", "/context", "/events", "/review", "/test", "/compact", "/undo", "/pause", "/resume", "/cancel", "/clear", "/quit", "/exit")
    choices = {"/mode": ("plan", "act", "bypass"), "/plan": ("show", "refresh"), "/model": ("show", "list", "select"), "/undo": ("latest",)}

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            words = document.text_before_cursor.split()
            if not words:
                return
            command = words[0].lower()
            if command in choices and (len(words) > 1 or document.text_before_cursor.endswith(" ")):
                prefix = words[-1] if not document.text_before_cursor.endswith(" ") else ""
                for item in choices[command]:
                    if item.startswith(prefix):
                        yield Completion(item, start_position=-len(prefix))
            elif words[-1].startswith("/"):
                for item in commands:
                    if item.startswith(words[-1]):
                        yield Completion(item, start_position=-len(words[-1]))

    transcript: list[str] = []
    lock = threading.Lock()
    app = None

    def append_output(value: str) -> None:
        clean = _ANSI.sub("", str(value)).replace("\r", "")
        with lock:
            transcript.extend(clean.splitlines() or ([clean] if clean else []))
            del transcript[:-2000]
        if app is not None:
            app.invalidate()

    class Capture:
        def write(self, value: str) -> int:
            append_output(value)
            return len(value)
        def flush(self) -> None:
            pass

    input_area = TextArea(height=3, multiline=True, prompt=HTML("<b>❯ </b>"), completer=SlashCompleter(), complete_while_typing=True)
    transcript_control = FormattedTextControl(lambda: [("", "\n".join(transcript[-2000:]))], focusable=False)
    status_control = FormattedTextControl(lambda: HTML(f"<b> ForgeCode </b>  {mode()}  │  Tab complete · Esc cancel · Ctrl-C quit"))
    bindings = KeyBindings()

    def submit() -> None:
        text = input_area.text
        input_area.buffer.reset()
        if not text.strip():
            return
        def worker() -> None:
            # Capture production renderer output only while dispatching.  The
            # TUI application's own stdout must remain connected to the
            # terminal or prompt-toolkit cannot repaint the screen.
            with redirect_stdout(Capture()):
                try:
                    value = session.dispatch(text)
                except SlashCommandError as exc:
                    value = {"error": str(exc)}
            if value is not None:
                rendered = _human_result(value)
                if rendered:
                    append_output(rendered)
            if session.stopped and app is not None:
                app.exit()
        threading.Thread(target=worker, daemon=True).start()

    @bindings.add("enter")
    def _(event):
        submit()

    @bindings.add("escape")
    def _(event):
        rendered = _human_result(session.cancel())
        if rendered:
            append_output(rendered)

    @bindings.add("c-c")
    def _(event):
        session.quit()
        event.app.exit()

    root = HSplit([
        Window(FormattedTextControl(lambda: "  FORGECODE // LIVE"), height=1, style="class:header"),
        Window(transcript_control, wrap_lines=True, always_hide_cursor=True),
        Window(FormattedTextControl(lambda: "─" * 80), height=1, style="class:rule"),
        input_area,
        Window(status_control, height=1, style="class:status"),
    ])
    app = Application(layout=Layout(root, focused_element=input_area), key_bindings=bindings, style=Style.from_dict({"header": "bold fg:#00d7ff bg:#202123", "rule": "fg:#444444", "status": "fg:#ffffff bg:#303030"}), full_screen=True)
    session.output = append_output
    session.raw_output = append_output
    session.input_bar = lambda: None
    app.run()
