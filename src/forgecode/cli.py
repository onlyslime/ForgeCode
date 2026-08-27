"""Command-line entry point for the framework skeleton."""

import argparse
from pathlib import Path
import sys

from . import __version__
from .config import Settings
from .security.workspace import WorkspaceGuard
from .tools import build_default_registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgecode", description="Self-built coding agent framework")
    parser.add_argument("--version", action="version", version=f"forgecode {__version__}")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="workspace root")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="check the local framework setup")
    subparsers.add_parser("tools", help="list built-in tool schemas")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        print(f"workspace is not a directory: {workspace}", file=sys.stderr)
        return 2

    settings = Settings.from_environment(workspace)
    guard = WorkspaceGuard(workspace)
    registry = build_default_registry(guard)
    command = args.command or "doctor"

    if command == "doctor":
        print(f"ForgeCode v{__version__}")
        print(f"workspace: {settings.workspace}")
        print(f"model: {settings.model or 'not configured (framework-only mode)'}")
        print("tools: " + ", ".join(registry.names()))
        print("status: ready")
        return 0

    if command == "tools":
        for definition in registry.definitions():
            print(f"{definition.name}: {definition.description}")
        return 0

    raise AssertionError(f"unhandled command: {command}")
