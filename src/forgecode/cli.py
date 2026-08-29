"""Stable CLI entry point kept small for downstream callers."""

from .application.commands import _parser, main

def fc_main() -> int:
    import sys
    # ``fcc`` is the chat launcher.  Keep launcher flags ergonomic by
    # translating ``fcc --bypass`` to the regular chat command contract.
    argv = sys.argv[1:]
    if "--bypass" in argv:
        argv = [item for item in argv if item != "--bypass"]
        sys.argv[1:] = ["chat", "--mode", "bypass", *argv]
    elif not argv:
        sys.argv.append("chat")
    elif argv[0].startswith("-"):
        sys.argv[1:] = ["chat", *argv]
    return main()

__all__ = ["_parser", "main", "fc_main"]
