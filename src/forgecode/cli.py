"""Stable CLI entry point kept small for downstream callers."""

from .application.commands import _parser, main

def fc_main() -> int:
    import sys
    # ``fcc`` is the chat launcher.  Keep launcher flags ergonomic by
    # translating ``fcc --bypass`` to the regular chat command contract.
    argv = sys.argv[1:]
    # ``--version`` and top-level help belong to the root parser and must be
    # passed through unchanged.  Previously ``fcc --version`` was rewritten
    # as ``chat --version`` (an invalid subcommand invocation).
    if "--version" in argv or (argv and argv[0] in {"-h", "--help"}):
        return main()
    launch_modes = {"--plan": "plan", "--act": "act", "--bypass": "bypass"}
    selected = [launch_modes[item] for item in argv if item in launch_modes]
    if selected:
        # A launcher mode is a convenience flag, not a second conflicting
        # command-line mode. Fail clearly when users provide two variants.
        if len(set(selected)) != 1:
            raise SystemExit("fcc: choose only one of --plan, --act, or --bypass")
        argv = [item for item in argv if item not in launch_modes]
        argv = _insert_chat_after_global_options(argv)
        # Keep the explicit mode next to the chat command so it cannot be
        # mistaken for a root-level option.
        command_index = argv.index("chat")
        argv[command_index + 1:command_index + 1] = ["--mode", selected[0]]
        sys.argv[1:] = argv
    elif not argv:
        sys.argv.append("chat")
    elif argv[0].startswith("-"):
        sys.argv[1:] = _insert_chat_after_global_options(argv)
    return main()


def _insert_chat_after_global_options(argv: list[str]) -> list[str]:
    """Insert the implicit ``chat`` command after root-level options.

    ``argparse`` only accepts global options before a subcommand.  The
    ``fcc`` launcher accepts the natural ``fcc --workspace DIR`` spelling, so
    preserve root options (including their values) before inserting ``chat``
    and leave all remaining flags for the chat parser.
    """
    root_with_value = {"--workspace"}
    root_flags = {"--json", "--jsonl"}
    prefix: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item.startswith("--workspace=") and len(item) > len("--workspace="):
            prefix.append(item)
            index += 1
            continue
        if item in root_flags:
            prefix.append(item)
            index += 1
            continue
        if item in root_with_value and index + 1 < len(argv):
            prefix.extend((item, argv[index + 1]))
            index += 2
            continue
        break
    return [*prefix, "chat", *argv[index:]]

__all__ = ["_parser", "main", "fc_main"]
