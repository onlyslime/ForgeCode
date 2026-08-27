"""Command-line entry point for the framework skeleton."""

import argparse
import asyncio
import os
from pathlib import Path
import sys
from typing import Any

from . import __version__
from .agent import AgentConfig, AgentLoop
from .config import Settings
from .models import DemoProvider, OpenAICompatibleProvider, ProviderError
from .security.workspace import WorkspaceGuard
from .storage import SessionStore
from .tools import InteractiveApproval, ToolContext, build_default_registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgecode", description="Self-built coding agent framework")
    parser.add_argument("--version", action="version", version=f"forgecode {__version__}")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="workspace root")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="check the local framework setup")
    subparsers.add_parser("tools", help="list built-in tool schemas")
    run_parser = subparsers.add_parser("run", help="run a coding task with the local agent")
    run_parser.add_argument("prompt", nargs="*", help="task prompt; omit to read it interactively")
    run_parser.add_argument("--max-steps", type=int, default=12)
    run_parser.add_argument("--session", type=Path, help="JSONL session path")
    run_parser.add_argument("--auto-approve", "--yes", action="store_true", help="approve writes and commands automatically")
    run_parser.add_argument("--verify", help="command to run after the model finishes editing")
    run_parser.add_argument("--demo", action="store_true", help="run the deterministic offline demonstration")
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

    if command == "run":
        prompt = " ".join(args.prompt).strip() or ("Run the offline ForgeCode demonstration." if args.demo else input("Task prompt: ").strip())
        api_key = os.getenv("FORGECODE_API_KEY", "")
        approval = InteractiveApproval(auto_approve=args.auto_approve or args.demo, secrets=[api_key])
        try:
            session_path = guard.resolve(args.session) if args.session else workspace / ".forgecode" / "sessions" / "latest.jsonl"
        except (OSError, ValueError) as exc:
            print(f"invalid session path: {exc}", file=sys.stderr)
            return 2
        session = SessionStore(session_path, secrets=[api_key])
        events: list[tuple[str, dict[str, Any]]] = []

        def on_event(kind: str, payload: dict[str, Any]) -> None:
            events.append((kind, payload))
            if kind == "tool_call":
                print(f"[tool] {payload['tool']} id={payload['id']} args={_safe_summary(payload.get('arguments', {}), [api_key])}")
            elif kind == "approval":
                print(f"[approval] {payload['tool']}: {'approved' if payload['approved'] else 'denied'}")
            elif kind == "tool_result":
                status = "ok" if payload["ok"] else "error"
                output = _redact_display(payload["output"], [api_key])[:500].replace("\n", "\\n")
                print(f"[result:{status}] {output}")
            elif kind == "verification_result":
                print(f"[verify] {'passed' if payload['ok'] else 'failed'}: {_redact_display(payload['output'], [api_key])[:500]}")
            elif kind == "error":
                print(f"[error] {_redact_display(payload['message'], [api_key])}", file=sys.stderr)
            elif kind == "model_message" and payload.get("content"):
                print(f"[model] {_redact_display(str(payload['content']), [api_key])[:1_000]}")

        try:
            provider = DemoProvider() if args.demo else OpenAICompatibleProvider.from_environment()
            context = ToolContext(guard, approval)
            verification_command = args.verify or ("python -c \"print('verification passed')\"" if args.demo else _default_verification_command(workspace))
            loop = AgentLoop(provider, registry, context, session=session, config=AgentConfig(max_steps=args.max_steps, verification_command=verification_command), on_event=on_event)
            result = asyncio.run(loop.run(prompt))
        except (ProviderError, ValueError, OSError) as exc:
            print(f"forgecode run failed: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
            return 1

        print(f"[final] stop={result.stopped_reason} verification={result.verification_ok}")
        final_messages = [message for message in result.messages if message.role == "assistant" and message.content]
        if final_messages:
            print(f"[final message] {_redact_display(final_messages[-1].content, [api_key])[:4_000]}")
        print(f"[session] {session_path}")
        status = _git_status(workspace)
        if status:
            print("[changed files]")
            print(status)
        diff = _git_diff(workspace)
        if diff:
            print("[diff]")
            print(_redact_display(diff, [api_key])[:8_000])
        return 0 if result.succeeded and result.verification_ok is not False else 1

    raise AssertionError(f"unhandled command: {command}")


def _git_diff(workspace: Path) -> str:
    import subprocess

    try:
        completed = subprocess.run(["git", "diff", "--no-ext-diff"], cwd=workspace, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _git_status(workspace: Path) -> str:
    import subprocess

    try:
        completed = subprocess.run(["git", "status", "--short"], cwd=workspace, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _default_verification_command(workspace: Path) -> str | None:
    """Use a conservative project test command when one is discoverable."""
    if (workspace / "pyproject.toml").exists() and (workspace / "tests").is_dir():
        return "uv run pytest"
    if (workspace / "package.json").exists():
        return "npm test"
    return None


def _safe_summary(arguments: dict[str, Any], secrets=()) -> str:
    values: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in {"content", "command"} and isinstance(value, str):
            values[key] = value[:120] + ("..." if len(value) > 120 else "")
        else:
            values[key] = value
    rendered = repr(values)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "[REDACTED]")
    return rendered.replace("Bearer ", "Bearer [REDACTED]")


def _redact_display(value: str, secrets=()) -> str:
    rendered = str(value)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "[REDACTED]")
    return rendered.replace("Bearer ", "Bearer [REDACTED]")
