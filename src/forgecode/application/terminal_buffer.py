"""Small stdlib terminal line editor used by the interactive chat UI."""
from __future__ import annotations

import sys


def read_line(prompt: str = "") -> str:
    """Read one editable line; preserve pasted newlines as one payload.

    Windows consoles expose pasted characters through ``msvcrt``. We keep a
    bounded buffer and only return on Enter; a pasted CRLF sequence is folded
    into a newline instead of dispatching each pasted line independently.
    """
    if sys.platform != "win32":
        return input(prompt)
    import msvcrt
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars: list[str] = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            # Drain characters already queued by a paste operation. This
            # turns pasted multiline text into one submitted message.
            while msvcrt.kbhit():
                nxt = msvcrt.getwch()
                if nxt in ("\r", "\n"):
                    chars.append("\n")
                elif nxt == "\x08":
                    if chars:
                        chars.pop()
                elif nxt and nxt not in ("\x00", "\xe0"):
                    chars.append(nxt)
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(chars)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x08":
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if ch in ("\x00", "\xe0"):
            # Consume extended-key scan code; arrows are intentionally
            # ignored until a full-screen editor is needed.
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        chars.append(ch)
        sys.stdout.write(ch)
        sys.stdout.flush()

