"""Command-line entry point for the framework skeleton."""

import argparse
import asyncio
import os
from pathlib import Path
import sys
import uuid
from typing import Any

from . import __version__
from .agent import AgentConfig, AgentLoop
from .context import RepositoryMapBuilder
from .config import Settings
from .models import DemoProvider, OpenAICompatibleProvider, ProviderError
from .security.redaction import redact_text
from .security.workspace import WorkspaceGuard
from .storage import CheckpointStore, SessionFormatError, SessionStore
from .tools import AgentMode, AllowAllApproval, InteractiveApproval, ToolContext, build_default_registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgecode", description="Self-built coding agent framework")
    parser.add_argument("--version", action="version", version=f"forgecode {__version__}")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="workspace root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON where supported")
    subparsers = parser.add_subparsers(dest="command")
    doctor_parser = subparsers.add_parser("doctor", help="check the local framework setup")
    doctor_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    tools_parser = subparsers.add_parser("tools", help="list built-in tool schemas")
    tools_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    inspect_parser = subparsers.add_parser("inspect", aliases=["map"], help="inspect a bounded read-only repository map")
    inspect_parser.add_argument("--task", default="repository inspection", help="task used to rank relevant files")
    inspect_parser.add_argument("--budget-chars", type=int, default=20_000)
    inspect_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    sessions_parser = subparsers.add_parser("sessions", help="list bounded local session records")
    sessions_parser.add_argument("--limit", type=int, default=50)
    sessions_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    diff_parser = subparsers.add_parser("diff", help="show the latest bounded agent change preview")
    diff_parser.add_argument("--session", type=Path, default=Path("latest"))
    diff_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    status_parser = subparsers.add_parser("status", help="show latest run and transaction status")
    status_parser.add_argument("--session", type=Path, default=Path("latest"))
    status_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    session_parser = subparsers.add_parser("session", help="inspect or export one session")
    session_sub = session_parser.add_subparsers(dest="session_action", required=True)
    show_parser = session_sub.add_parser("show", help="show session metadata and event summary")
    show_parser.add_argument("session_id", type=Path)
    show_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    export_parser = session_sub.add_parser("export", help="export bounded redacted JSONL")
    export_parser.add_argument("session_id", type=Path)
    export_parser.add_argument("--max-chars", type=int, default=200_000)
    export_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    run_parser = subparsers.add_parser("run", help="run a coding task with the local agent")
    run_parser.add_argument("prompt", nargs="*", help="task prompt; omit to read it interactively")
    run_parser.add_argument("--max-steps", type=int, default=12)
    run_parser.add_argument("--session", type=Path, help="JSONL session path")
    run_parser.add_argument("--auto-approve", "--yes", action="store_true", help="approve writes and commands automatically")
    run_parser.add_argument("--verify", help="command to run after the model finishes editing")
    run_parser.add_argument("--no-verify", action="store_true", help="disable automatic verification explicitly")
    run_parser.add_argument("--demo", action="store_true", help="run the deterministic offline demonstration")
    run_parser.add_argument("--demo-task", choices=("calculator", "json"), default="calculator", help="offline demo scenario")
    run_parser.add_argument("--mode", choices=[mode.value for mode in AgentMode], default=AgentMode.ACT.value, help="execution mode: plan is read-only; act permits approved side effects")
    run_parser.add_argument("--resume", type=Path, help="resume safely from a session id or JSONL path")
    run_parser.add_argument("--dry-run", "--inspect", action="store_true", help="inspect a resume without executing side effects")
    run_parser.add_argument("--force-recovery", action="store_true", help="explicitly acknowledge checkpoint conflicts (still requires approval)")
    run_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
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
        if args.json:
            import json
            print(json.dumps({"version": __version__, "workspace": ".", "model": settings.model, "configured": bool(settings.model), "tools": list(registry.names()), "status": "ready"}, ensure_ascii=False))
            return 0
        print(f"ForgeCode v{__version__}")
        print("workspace: .")
        print(f"model: {settings.model or 'not configured (framework-only mode)'}")
        print("tools: " + ", ".join(registry.names()))
        print("status: ready")
        return 0

    if command == "tools":
        if args.json:
            import json
            print(json.dumps([{"name": definition.name, "description": definition.description, "side_effecting": definition.side_effecting, "parameters": definition.parameters} for definition in registry.definitions()], ensure_ascii=False))
            return 0
        for definition in registry.definitions():
            print(f"{definition.name}: {definition.description}")
        return 0

    if command in {"inspect", "map"}:
        try:
            repository = RepositoryMapBuilder(guard).build()
            context_plan = repository.plan_context(args.task, budget_chars=args.budget_chars)
        except (OSError, ValueError) as exc:
            print(f"inspect failed: {_redact_display(str(exc))}", file=sys.stderr)
            return 2
        if args.json:
            import json
            print(json.dumps({"snapshot": repository.to_dict(), "context": {"selected_paths": list(context_plan.selected_paths), "omitted": context_plan.omitted, "budget_chars": context_plan.budget_chars, "rendered": context_plan.render()}}, ensure_ascii=False, default=str))
        else:
            print(context_plan.render())
            print(f"[map] files={len(repository.snapshot.files)} omitted={repository.snapshot.omitted} errors={len(repository.snapshot.errors)}")
        return 0

    if command == "sessions":
        if isinstance(args.limit, bool) or args.limit < 1 or args.limit > 200:
            print("limit must be between 1 and 200", file=sys.stderr)
            return 2
        directory = workspace / ".forgecode" / "sessions"
        entries = sorted((path for path in directory.glob("*.jsonl") if path.is_file()), key=lambda path: path.name, reverse=True)[: args.limit] if directory.is_dir() else []
        rows = []
        for path in entries:
            store = SessionStore(path)
            result = store.read_with_issues()
            last = result.events[-1] if result.events else None
            state = _last_session_state(result.events)
            rows.append({"id": path.stem, "path": guard.relative(path), "events": len(result.events), "issues": len(result.issues), "state": state or (last.payload.get("state") if last else None)})
        if args.json:
            import json
            print(json.dumps(rows, ensure_ascii=False))
        else:
            if not rows:
                print("no sessions")
            for row in rows:
                print(f"{row['id']} events={row['events']} issues={row['issues']} state={row['state'] or 'unknown'}")
        return 0

    if command in {"diff", "status"}:
        try:
            path = _resolve_session_reference(guard, workspace, args.session)
            result = SessionStore(path).read_with_issues()
        except (OSError, ValueError) as exc:
            print(f"session unavailable: {_redact_display(str(exc))}", file=sys.stderr)
            return 2
        previews = [event for event in result.events if event.kind == "patch_preview"]
        transactions = [event for event in result.events if event.kind in {"patch_commit", "patch_rollback", "patch_refused"}]
        if command == "diff":
            preview = previews[-1].payload.get("preview", "") if previews else ""
            payload = {"session": path.stem, "transaction_id": previews[-1].payload.get("transaction_id") if previews else None, "diff": preview, "truncated": "[patch preview truncated]" in str(preview)}
            if args.json:
                import json
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(preview or "no patch preview recorded")
            return 0
        last_state = _last_session_state(result.events) or "unknown"
        payload = {"session": path.stem, "state": last_state, "transactions": [{"kind": event.kind, **event.payload} for event in transactions[-20:]], "issues": len(result.issues), "rollback_available": False}
        if args.json:
            import json
            print(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            print(f"session={path.stem} state={last_state} transactions={len(transactions)} issues={len(result.issues)}")
            for event in transactions[-20:]:
                print(f"{event.kind} transaction={event.payload.get('transaction_id')} ok={event.payload.get('ok')}")
            print("rollback_available=false (automatic rollback is performed only for in-process write failures)")
        return 0 if not result.issues else 1

    if command == "session":
        try:
            session_path = _resolve_session_reference(guard, workspace, args.session_id)
            store = SessionStore(session_path)
            if args.session_action == "export":
                output = store.export(max_chars=args.max_chars)
                if args.json:
                    import json
                    print(json.dumps({"path": guard.relative(session_path), "events_jsonl": output, "issues": [issue.__dict__ for issue in store.last_read_issues]}, ensure_ascii=False))
                else:
                    print(output, end="")
                return 0
            result = store.read_with_issues()
            last_state = _last_session_state(result.events)
            summary = {"id": session_path.stem, "path": guard.relative(session_path), "run_id": result.events[0].run_id if result.events else store.run_id, "events": len(result.events), "issues": [issue.__dict__ for issue in result.issues], "state": last_state or "unknown", "kinds": sorted({event.kind for event in result.events})}
            if args.json:
                import json
                print(json.dumps(summary, ensure_ascii=False))
            else:
                print(f"session {summary['id']} run_id={summary['run_id']} events={summary['events']} state={summary['state']}")
                if summary["issues"]:
                    print("issues: " + "; ".join(f"line {issue['line']}: {issue['message']}" for issue in summary["issues"][:10]))
                print("kinds: " + ", ".join(summary["kinds"]))
            return 0 if not result.issues else 1
        except (OSError, ValueError, SessionFormatError) as exc:
            print(f"invalid session: {_redact_display(str(exc))}", file=sys.stderr)
            return 2

    if command == "run":
        if args.dry_run and not args.resume:
            print("--dry-run requires --resume SESSION", file=sys.stderr)
            return 2
        if args.force_recovery and not args.resume:
            print("--force-recovery requires --resume SESSION", file=sys.stderr)
            return 2
        if args.demo and args.resume:
            print("--demo cannot be combined with --resume", file=sys.stderr)
            return 2
        if args.verify and args.no_verify:
            print("--verify cannot be combined with --no-verify", file=sys.stderr)
            return 2
        if args.mode == AgentMode.PLAN.value and args.verify:
            print("--verify is unavailable in plan mode; omit it or switch to act", file=sys.stderr)
            return 2
        prompt = " ".join(args.prompt).strip()
        if not prompt:
            if args.demo:
                prompt = "Run the offline ForgeCode demonstration."
            elif args.resume or args.dry_run:
                prompt = "Inspect and safely resume the checkpoint."
            else:
                try:
                    prompt = input("Task prompt: ").strip()
                except EOFError:
                    print("task prompt is required when stdin is unavailable", file=sys.stderr)
                    return 2
                except KeyboardInterrupt:
                    print("task input cancelled", file=sys.stderr)
                    return 130
        api_key = os.getenv("FORGECODE_API_KEY", "")
        approval = InteractiveApproval(auto_approve=args.auto_approve or args.demo, secrets=[api_key])
        new_run_id = uuid.uuid4().hex
        try:
            if args.resume:
                session_path = _resolve_session_reference(guard, workspace, args.resume)
            else:
                session_path = _resolve_session_reference(guard, workspace, args.session) if args.session else workspace / ".forgecode" / "sessions" / f"{new_run_id}.jsonl"
        except (OSError, ValueError) as exc:
            print(f"invalid session path: {exc}", file=sys.stderr)
            return 2
        session = SessionStore(session_path, secrets=[api_key], run_id=None if args.resume else new_run_id, mode=args.mode)
        if args.resume:
            try:
                checkpoint = CheckpointStore(session_path.with_suffix(".checkpoint.json")).load()
                conflicts = CheckpointStore(session_path.with_suffix(".checkpoint.json")).validate(checkpoint, guard, expected_run_id=session.run_id)
            except FileNotFoundError:
                print(f"resume checkpoint not found for {session_path.stem}", file=sys.stderr)
                return 2
            except (OSError, ValueError) as exc:
                print(f"resume checkpoint invalid: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
                return 2
            if conflicts:
                _print_recovery(conflicts, json_mode=args.json)
                try:
                    session.append("recovery_conflict", {"state": "recovery_required", "conflicts": [conflict.__dict__ for conflict in conflicts]}, mode=args.mode, error_code="recovery_conflict")
                except OSError as exc:
                    print(f"could not record recovery conflict: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
                    return 1
                if args.dry_run:
                    payload = {"run_id": checkpoint.run_id, "state": "recovery_required", "mode": checkpoint.mode, "pending_actions": list(checkpoint.pending_actions), "files": [item.path for item in checkpoint.files], "conflicts": [conflict.__dict__ for conflict in conflicts]}
                    if args.json:
                        import json
                        print(json.dumps(payload, ensure_ascii=False))
                    return 3
                if not args.force_recovery:
                    try:
                        session.append("state_transition", {"from": checkpoint.state, "to": "recovery_required", "reason": "checkpoint conflict"}, mode=args.mode, error_code="recovery_conflict")
                    except OSError:
                        pass
                    return 3
                try:
                    session.append("recovery_override", {"force_recovery": True, "conflicts": [conflict.__dict__ for conflict in conflicts]}, mode=args.mode, outcome="requires fresh approval")
                except OSError as exc:
                    print(f"could not record recovery override: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
                    return 1
                # A conflict override always requires fresh interactive
                # approval; --auto-approve cannot silently cover it.
                approval = InteractiveApproval(auto_approve=False, secrets=[api_key])
            if args.dry_run:
                payload = {"run_id": checkpoint.run_id, "state": checkpoint.state, "mode": checkpoint.mode, "pending_actions": list(checkpoint.pending_actions), "files": [item.path for item in checkpoint.files], "conflicts": [conflict.__dict__ for conflict in conflicts]}
                if args.json:
                    import json
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    print(f"resume preview: run_id={checkpoint.run_id} state={checkpoint.state} files={len(checkpoint.files)} conflicts={len(conflicts)}")
                    if checkpoint.pending_actions:
                        print(f"pending actions require fresh approval: {len(checkpoint.pending_actions)}")
                return 3 if conflicts else 0
            prompt = ("Resume safely from checkpoint. Do not replay any previously executed write or command; "
                      "rebuild context, inspect current files, and request fresh approval for pending side effects.\n" + prompt)
        if args.demo and args.mode == AgentMode.ACT.value:
            try:
                _prepare_demo_workspace(registry, guard, task=args.demo_task)
            except OSError as exc:
                print(f"forgecode run failed: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
                return 1
        events: list[tuple[str, dict[str, Any]]] = []

        def on_event(kind: str, payload: dict[str, Any]) -> None:
            events.append((kind, payload))
            if args.json:
                return
            if kind == "mode":
                print(f"[mode] {payload['mode']} (side effects {'enabled' if payload['side_effects_allowed'] else 'disabled'})")
            elif kind == "tool_call":
                print(f"[tool] {payload['tool']} id={payload['id']} args={_safe_summary(payload.get('arguments', {}), [api_key])}")
            elif kind == "approval":
                print(f"[approval] {payload['tool']}: {'approved' if payload['approved'] else 'denied'}")
            elif kind == "tool_result":
                status = "ok" if payload["ok"] else "error"
                output = _redact_display(payload["output"], [api_key])[:500].replace("\n", "\\n")
                risk = payload.get("metadata", {}).get("risk")
                risk_label = f" risk={risk}" if risk else ""
                print(f"[result:{status}{risk_label}] {output}")
                patch_preview = payload.get("metadata", {}).get("diff")
                if patch_preview:
                    print(f"[patch] {_redact_display(patch_preview, [api_key])[:4_000]}")
            elif kind == "verification_result":
                print(f"[verify] {'passed' if payload['ok'] else 'failed'}: {_redact_display(payload['output'], [api_key])[:500]}")
            elif kind == "error":
                print(f"[error] {_redact_display(payload['message'], [api_key])}", file=sys.stderr)
            elif kind == "session_error":
                print(
                    f"[session error] event={payload.get('event')}: {_redact_display(payload.get('error', ''), [api_key])}",
                    file=sys.stderr,
                )

        try:
            provider = DemoProvider(args.demo_task) if args.demo else OpenAICompatibleProvider.from_environment()
            context = ToolContext(guard, approval, mode=args.mode, secrets=tuple(secret for secret in (api_key,) if secret))
            demo_verification = "python -B -m pytest -q test_demo_calculator.py" if args.demo_task == "calculator" else "python -B -m pytest -q test_demo_config.py"
            verification_command = None if args.no_verify else (args.verify or (demo_verification if args.demo else _default_verification_command(workspace)))
            loop = AgentLoop(provider, registry, context, session=session, config=AgentConfig(max_steps=args.max_steps, verification_command=verification_command), on_event=on_event)
            result = asyncio.run(loop.run(prompt))
        except (ProviderError, ValueError, OSError) as exc:
            print(f"forgecode run failed: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
            return 1

        if not args.json:
            print(f"[final] stop={result.stopped_reason} verification={result.verification_ok}")
            print(f"[mode] {result.mode}")
            if result.plan_summary is not None:
                print(f"[plan] {_redact_display(result.plan_summary, [api_key])[:4_000]}")
                print("[plan] no files or commands were executed")
            if result.explored:
                print("[explored] " + ", ".join(_redact_display(item, [api_key]) for item in result.explored[:100]))
            final_messages = [message for message in result.messages if message.role == "assistant" and message.content]
            if final_messages:
                print(f"[final message] {_redact_display(final_messages[-1].content, [api_key])[:4_000]}")
            print(f"[session] {guard.relative(session_path)}")
            status = _git_status(workspace)
            if status:
                print("[changed files]")
                print(status)
            diff = _git_diff(workspace)
            if diff:
                print("[diff]")
                print(_redact_display(diff, [api_key])[:8_000])
        if args.json:
            import json
            print(json.dumps({"stopped_reason": result.stopped_reason, "state": result.state, "run_id": result.run_id, "verification_ok": result.verification_ok, "succeeded": result.succeeded, "audit_complete": result.audit_complete}, ensure_ascii=False))
        if not result.audit_complete:
            print("[final] session audit incomplete", file=sys.stderr)
        return 0 if result.succeeded and result.verification_ok is not False and result.audit_complete else 1

    raise AssertionError(f"unhandled command: {command}")


def _resolve_session_reference(guard: WorkspaceGuard, workspace: Path, reference: Path) -> Path:
    value = str(reference)
    if not value:
        raise ValueError("session reference must not be empty")
    candidate = reference
    sessions_directory = workspace / ".forgecode" / "sessions"
    if value == "latest":
        candidates = sorted((path for path in sessions_directory.glob("*.jsonl") if path.is_file()), key=lambda path: path.stat().st_mtime_ns, reverse=True) if sessions_directory.is_dir() else []
        if not candidates:
            raise FileNotFoundError("no session records exist")
        candidate = candidates[0]
    elif reference.suffix.lower() != ".jsonl":
        candidate = Path(".forgecode") / "sessions" / f"{value}.jsonl"
    resolved = guard.resolve(candidate)
    if resolved.suffix.lower() != ".jsonl" or resolved.parent != (workspace / ".forgecode" / "sessions").resolve():
        raise ValueError("session must be a JSONL file under .forgecode/sessions")
    return resolved


def _last_session_state(events) -> str | None:
    """Return the newest explicit run state, including recovery conflicts."""
    for event in reversed(events):
        if event.kind == "state_transition":
            target = event.payload.get("to")
            if isinstance(target, str) and target:
                return target
        state = event.payload.get("state")
        if isinstance(state, str) and state:
            return state
    return None


def _print_recovery(conflicts, *, json_mode: bool = False) -> None:
    if json_mode:
        import json
        print(json.dumps({"recovery_required": True, "conflicts": [conflict.__dict__ for conflict in conflicts]}, ensure_ascii=False), file=sys.stderr)
    else:
        print("recovery required; no side effects were executed", file=sys.stderr)
        for conflict in conflicts[:20]:
            print(f"- {conflict.path}: {conflict.reason}", file=sys.stderr)


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
    return redact_text(repr(values), secrets)


def _redact_display(value: str, secrets=()) -> str:
    return redact_text(value, secrets)


def _prepare_demo_workspace(registry, guard: WorkspaceGuard, *, task: str = "calculator") -> None:
    """Create a tiny intentionally broken fixture through the normal write tool."""
    fixtures_by_task = {
        "calculator": {
            "demo_calculator.py": "def add(a, b):\n    return a - b\n",
            "test_demo_calculator.py": "from demo_calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
        "json": {
            "demo_config.json": '{\n  "name": "ForgeCode demo",\n  "enabled": false\n}\n',
            "test_demo_config.py": 'import json\nfrom pathlib import Path\n\ndef test_enabled():\n    config = json.loads(Path("demo_config.json").read_text(encoding="utf-8"))\n    assert config["enabled"] is True\n',
        },
    }
    if task not in fixtures_by_task:
        raise OSError(f"unknown demo task: {task}")
    fixtures = fixtures_by_task[task]
    context = ToolContext(guard, AllowAllApproval(), mode=AgentMode.ACT)
    paths = {name: guard.resolve(name) for name in fixtures}
    conflicts = [name for name, path in paths.items() if path.exists()]
    if conflicts:
        raise OSError(f"demo workspace already contains {', '.join(conflicts)}; use a fresh workspace")
    for name, content in fixtures.items():
        path = paths[name]
        result = registry.execute("write_file", {"path": name, "content": content}, context)
        if not result.ok:
            raise OSError(f"could not prepare demo fixture {name}: {result.output}")
