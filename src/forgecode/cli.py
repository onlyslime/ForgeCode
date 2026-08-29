"""Stable CLI entry point kept small for downstream callers."""

from .application.commands import _parser, main

def fc_main() -> int:
    import sys
    # ``fcc`` is the chat launcher.  Keep launcher flags ergonomic by
    # translating ``fcc --bypass`` to the regular chat command contract.
    argv = sys.argv[1:]
    launch_modes = {"--plan": "plan", "--act": "act", "--bypass": "bypass"}
    selected = [launch_modes[item] for item in argv if item in launch_modes]
    if selected:
        # A launcher mode is a convenience flag, not a second conflicting
        # command-line mode. Fail clearly when users provide two variants.
        if len(set(selected)) != 1:
            raise SystemExit("fcc: choose only one of --plan, --act, or --bypass")
        argv = [item for item in argv if item not in launch_modes]
        sys.argv[1:] = ["chat", "--mode", selected[0], *argv]
    elif not argv:
        sys.argv.append("chat")
    elif argv[0].startswith("-"):
        sys.argv[1:] = ["chat", *argv]
    return main()

__all__ = ["_parser", "main", "fc_main"]
