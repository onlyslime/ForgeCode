"""Stable CLI entry point kept small for downstream callers."""

from .application.commands import _parser, main

def fc_main() -> int:
    import sys
    if not sys.argv[1:]:
        sys.argv.append("chat")
    return main()

__all__ = ["_parser", "main", "fc_main"]
