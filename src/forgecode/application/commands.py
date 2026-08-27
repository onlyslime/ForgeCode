"""Command-line entry point for the framework skeleton."""

import argparse
import asyncio
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid
from typing import Any

from .. import __version__
from ..agent import AgentConfig, AgentLoop, ContextCompactor, RunState, SessionContextRebuilder
from .interactive_service import InteractiveSession
from .run_service import RunService
from .session_service import aggregate_events
from ..context import RepositoryMapBuilder
from ..config import ConfigError, ConfigLoader, Settings
from ..references import ReferenceResolver, parse_references
from ..rules import RuleEngine
from ..plan import PlanItem, TaskPlan
from ..storage import TransactionError, TransactionStore
from ..models import DemoProvider, OpenAICompatibleProvider, ProviderError
from ..security.redaction import redact_text
from ..security.workspace import WorkspaceGuard
from ..storage import Checkpoint, CheckpointStore, RecoveryConflict, SessionFormatError, SessionStore
from ..tools import AgentMode, AllowAllApproval, DenyAllApproval, InteractiveApproval, ToolContext, build_default_registry


def _approval_output(json_mode: bool):
    """Keep interactive approval prompts off machine-readable stdout."""
    return (lambda message: print(message, file=sys.stderr)) if json_mode else print


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
    rules_parser = subparsers.add_parser("rules", help="show bounded scoped project rules")
    rules_sub = rules_parser.add_subparsers(dest="rules_action", required=False)
    rules_show = rules_sub.add_parser("show", help="show rule sources and combined context")
    rules_show.add_argument("targets", nargs="*", help="optional files/directories whose nested rules apply")
    rules_show.add_argument("--compatible", action="store_true", help="also inspect explicitly compatible rule file names")
    rules_show.add_argument("--include-text", action="store_true", help="include bounded rule text")
    rules_show.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    rules_check = rules_sub.add_parser("check", help="validate rule sources without executing them")
    rules_check.add_argument("targets", nargs="*")
    rules_check.add_argument("--compatible", action="store_true")
    rules_check.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    config_parser = subparsers.add_parser("config", help="inspect or validate typed effective configuration")
    config_sub = config_parser.add_subparsers(dest="config_action", required=True)
    config_show = config_sub.add_parser("show", help="show redacted effective config")
    config_show.add_argument("--profile")
    config_show.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    config_validate = config_sub.add_parser("validate", help="validate ignored TOML and environment config")
    config_validate.add_argument("--profile")
    config_validate.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    transaction_parser = subparsers.add_parser("transaction", aliases=["review", "rollback"], help="review or safely undo a recorded transaction")
    transaction_parser.add_argument("transaction_id", nargs="?", default="latest")
    transaction_parser.add_argument("--execute", action="store_true", help="execute undo after approval")
    transaction_parser.add_argument("--auto-approve", "--yes", action="store_true")
    transaction_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    chat_parser = subparsers.add_parser("chat", aliases=["start"], help="scriptable interactive coding session")
    chat_parser.add_argument("prompt", nargs="*", help="optional initial task")
    chat_parser.add_argument("--mode", choices=[mode.value for mode in AgentMode], default=AgentMode.PLAN.value)
    chat_parser.add_argument("--auto-approve", "--yes", action="store_true")
    chat_parser.add_argument("--demo", action="store_true")
    chat_parser.add_argument("--demo-task", choices=("calculator", "json"), default="calculator")
    chat_parser.add_argument("--session", type=Path)
    chat_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
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
    inspect_session_parser = session_sub.add_parser("inspect", help="rebuild bounded provider-neutral context")
    inspect_session_parser.add_argument("session_id", type=Path)
    inspect_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    compact_session_parser = session_sub.add_parser("compact", help="append a deterministic context summary")
    compact_session_parser.add_argument("session_id", type=Path)
    compact_session_parser.add_argument("--max-chars", type=int, default=24_000)
    compact_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    fork_session_parser = session_sub.add_parser("fork", help="create a new run linked to a parent session")
    fork_session_parser.add_argument("session_id", type=Path)
    fork_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    plan_parser = subparsers.add_parser("plan", help="create a bounded structured plan without side effects")
    plan_parser.add_argument("prompt", nargs="*", help="task to plan; omit with --session to inspect a stored plan")
    plan_parser.add_argument("--session", type=Path, help="show the latest structured plan event from a session")
    plan_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    run_parser = subparsers.add_parser("run", help="run a coding task with the local agent")
    run_parser.add_argument("prompt", nargs="*", help="task prompt; omit to read it interactively")
    run_parser.add_argument("--max-steps", type=int)
    run_parser.add_argument("--session", type=Path, help="JSONL session path")
    run_parser.add_argument("--auto-approve", "--yes", action="store_true", help="approve writes and commands automatically")
    run_parser.add_argument("--verify", help="command to run after the model finishes editing")
    run_parser.add_argument("--no-verify", action="store_true", help="disable automatic verification explicitly")
    run_parser.add_argument("--demo", action="store_true", help="run the deterministic offline demonstration")
    run_parser.add_argument("--demo-task", choices=("calculator", "json"), default="calculator", help="offline demo scenario")
    run_parser.add_argument("--mode", choices=[mode.value for mode in AgentMode], help="execution mode: plan is read-only; act permits approved side effects")
    run_parser.add_argument("--profile", help="named model/config profile")
    run_parser.add_argument("--resume", type=Path, help="resume safely from a session id or JSONL path")
    run_parser.add_argument("--fork", action="store_true", help="fork a completed/paused session into a new run id")
    run_parser.add_argument("--dry-run", "--inspect", action="store_true", help="inspect a resume without executing side effects")
    run_parser.add_argument("--force-recovery", action="store_true", help="explicitly acknowledge checkpoint conflicts (still requires approval)")
    run_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "doctor"
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        print(f"workspace is not a directory: {workspace}", file=sys.stderr)
        return 2

    try:
        selected_profile = getattr(args, "profile", None)
        if selected_profile:
            effective = ConfigLoader(workspace).load(profile=selected_profile)
            settings = Settings(workspace=workspace, model=effective.model, api_key_env=effective.api_key_env, base_url=effective.base_url, profile=effective.profile, effective=effective)
        elif command == "config":
            # The config subcommands must report malformed config as a
            # structured result themselves; do not fail during global CLI
            # bootstrap before their renderer gets control.
            settings = Settings(workspace=workspace)
        else:
            settings = Settings.from_environment(workspace)
    except ConfigError as exc:
        print(f"configuration invalid: {_redact_display(str(exc))}", file=sys.stderr)
        return 2
    guard = WorkspaceGuard(workspace)
    registry = build_default_registry(guard)
    if settings.effective is not None:
        registry = registry.filter(settings.effective.tool_policy)
    if command == "run":
        args.mode = args.mode or (settings.effective.default_mode if settings.effective else AgentMode.ACT.value)
        args.max_steps = args.max_steps if args.max_steps is not None else (settings.effective.max_steps if settings.effective else 12)

    if command == "config":
        try:
            config = ConfigLoader(workspace).load(profile=getattr(args, "profile", None))
            payload = {"valid": True, "config": config.to_dict(), "sources": list(config.sources)}
        except ConfigError as exc:
            payload = {"valid": False, "error": str(exc)}
            if args.config_action == "validate":
                if args.json:
                    import json
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    print("invalid: " + str(exc), file=sys.stderr)
                return 2
            if args.json:
                import json
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print("invalid: " + str(exc))
            return 2
        if args.json:
            import json
            print(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            print("configuration: valid")
            for key, value in config.to_dict().items():
                print(f"{key}: {value}")
        return 0

    if command == "rules":
        action = args.rules_action or "show"
        try:
            rules = RuleEngine(guard, compatible=getattr(args, "compatible", False)).discover(getattr(args, "targets", ()))
        except (OSError, ValueError) as exc:
            print(f"rules failed: {_redact_display(str(exc))}", file=sys.stderr)
            return 2
        payload = rules.to_dict(include_text=bool(getattr(args, "include_text", False) and action == "show"))
        if action == "check":
            payload["valid"] = not any(item.severity == "error" for item in rules.diagnostics)
        if args.json:
            import json
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"rules sources={len(rules.sources)} diagnostics={len(rules.diagnostics)} fingerprint={rules.fingerprint[:16]}")
            for source in rules.sources:
                print(f"{source.path} scope={source.scope} priority={source.priority} chars={source.chars} digest={source.digest[:16]}")
            for diagnostic in rules.diagnostics:
                print(f"{diagnostic.severity}: {diagnostic.code}: {diagnostic.message}")
            if getattr(args, "include_text", False) and rules.text:
                print(rules.render())
        return 0 if not any(item.severity == "error" for item in rules.diagnostics) else 1

    if command == "plan":
        prompt = " ".join(args.prompt).strip()
        try:
            if args.session:
                session_path = _resolve_session_reference(guard, workspace, args.session)
                events = SessionStore(session_path).read_with_issues().events
                event = next((item for item in reversed(events) if item.kind in {"plan_created", "plan_updated"} and isinstance(item.payload.get("plan"), dict)), None)
                if event is None:
                    raise ValueError("session has no structured plan")
                payload = {"session": session_path.stem, "sequence": event.sequence, "plan": event.payload["plan"]}
                plan = TaskPlan.from_dict(event.payload["plan"])
            else:
                if not prompt:
                    raise ValueError("a task prompt or --session is required")
                references = ReferenceResolver(guard).resolve_prompt(prompt)
                # Use the same target-scoped rule discovery as the run path.
                # Otherwise an independent plan would omit nested AGENTS.md
                # sources and produce a fingerprint that Act cannot trust.
                reference_targets = [item.path for item in references.items if item.path]
                rules = RuleEngine(guard).discover(reference_targets)
                if rules.has_errors:
                    raise ValueError("project rules contain fatal diagnostics; run `rules check` for details")
                if references.has_errors:
                    raise ValueError("explicit context references contain fatal diagnostics")
                plan = TaskPlan(task=prompt, mode="plan", rules_fingerprint=rules.fingerprint, context_fingerprint=references.fingerprint, items=(PlanItem("task-1", "Complete requested task", prompt[:2_000], expected_files=tuple(item.path for item in references.items if item.path), acceptance_criteria=("Inspect exact current files", "Verify with a real command")),))
                payload = {"plan": plan.to_dict(), "rules": rules.to_dict(), "references": {"items": [item.to_dict() for item in references.items], "diagnostics": [item.to_dict() for item in references.diagnostics]}}
        except (OSError, ValueError) as exc:
            message = _redact_display(str(exc))
            if args.json:
                import json
                print(json.dumps({"ok": False, "error": "plan_invalid", "message": message}, ensure_ascii=False))
            else:
                print(f"plan failed: {message}", file=sys.stderr)
            return 2
        if args.json:
            import json
            print(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            print(f"plan={plan.plan_id} revision={plan.revision} mode=plan stale={plan.stale}")
            for item in plan.items:
                print(f"{item.id}: {item.title} [{item.status}] risk={item.risk}")
            print("No files or commands were executed.")
        return 0

    if command in {"transaction", "review", "rollback"}:
        try:
            store = TransactionStore(guard)
            transaction_id = args.transaction_id
            if command == "rollback" or getattr(args, "execute", False):
                manifest = store.latest() if transaction_id == "latest" else store.load(transaction_id)
                api_key = os.getenv("FORGECODE_API_KEY", "")
                approval = InteractiveApproval(auto_approve=getattr(args, "auto_approve", False), output_fn=_approval_output(args.json), prompt_to_output=args.json, secrets=[api_key])
                if not getattr(args, "execute", False) and command != "rollback":
                    payload = store.review(transaction_id)
                else:
                    undone = store.undo(manifest.transaction_id, approval=approval, run_id=uuid.uuid4().hex)
                    payload = {"ok": True, "transaction_id": undone.transaction_id, "parent_transaction_id": manifest.transaction_id, "state": undone.state}
            else:
                payload = store.review(transaction_id)
        except (TransactionError, OSError, ValueError) as exc:
            payload = {"ok": False, "error": _redact_display(str(exc))}
            if args.json:
                import json
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print("transaction unavailable: " + payload["error"], file=sys.stderr)
            return 3 if "conflict" in str(exc).lower() or "hash" in str(exc).lower() else 2
        if args.json:
            import json
            print(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            print(f"transaction={payload.get('transaction_id')} state={payload.get('state')} rollback_available={payload.get('rollback_available', payload.get('ok', False))}")
            if payload.get("preview"):
                print(payload["preview"])
            if payload.get("conflicts"):
                print("conflicts: " + "; ".join(payload["conflicts"]))
        return 0

    if command in {"chat", "start"}:
        # The REPL is scriptable by design: callers may pipe lines through
        # stdin, while tests can inject a stream through ``InteractiveSession``.
        initial_prompt = " ".join(args.prompt).strip()
        api_key = os.getenv("FORGECODE_API_KEY", "")
        new_run_id = uuid.uuid4().hex
        session_path = _resolve_session_reference(guard, workspace, args.session) if args.session else _new_session_path(guard, new_run_id)
        # ``chat --session`` is intentionally a create-only entry point.  A
        # non-empty existing JSONL stream belongs to an earlier run and must
        # not receive a new run id with continued sequence numbers: doing so
        # would create a mixed-run audit stream.  Use the explicit ``run
        # --resume`` or ``session fork`` workflow when continuing a session.
        if args.session and session_path.is_file() and session_path.stat().st_size > 0:
            try:
                existing = SessionStore(session_path, secrets=[api_key]).read_with_issues(strict=True)
            except (OSError, SessionFormatError, ValueError) as exc:
                message = f"chat session is not a new session: {_redact_display(str(exc), [api_key])}"
                if args.json:
                    import json
                    print(json.dumps({"ok": False, "error": "session_not_new", "message": message}, ensure_ascii=False))
                else:
                    print(message + "; use run --resume or session fork", file=sys.stderr)
                return 3
            if existing.events or existing.issues:
                payload = {
                    "ok": False,
                    "error": "session_not_new",
                    "message": "chat --session refuses to append to an existing session; use run --resume or session fork",
                    "session": session_path.stem,
                    "events": len(existing.events),
                    "issues": len(existing.issues),
                }
                if args.json:
                    import json
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    print(payload["message"], file=sys.stderr)
                return 3
        session = SessionStore(session_path, secrets=[api_key], run_id=new_run_id, mode=args.mode)
        transaction_store = TransactionStore(guard)
        configured_approval = settings.effective.approval if settings.effective else "interactive"
        approval = DenyAllApproval() if configured_approval == "deny" and not (args.auto_approve or args.demo) else InteractiveApproval(auto_approve=args.auto_approve or args.demo or configured_approval == "auto", output_fn=_approval_output(args.json), prompt_to_output=args.json, secrets=[api_key])
        state = {"mode": args.mode, "last": None, "plan": None, "plan_targets": (), "reference_specs": (), "rules_fingerprint": "", "reference_fingerprint": "", "last_message": "", "last_verification": None}

        def run_message(message: str) -> Any:
            if not message.strip():
                return {"error": "message must not be empty"}
            try:
                references = ReferenceResolver(guard).resolve_prompt(message)
                target_paths = [item.path for item in references.items if item.path]
                rules = RuleEngine(guard).discover(target_paths)
                if rules.has_errors:
                    return {"error": "project rules contain fatal diagnostics; run /rules for details", "diagnostics": [item.to_dict() for item in rules.diagnostics if item.severity == "error"]}
                if references.has_errors:
                    return {"error": "explicit context references contain fatal diagnostics", "diagnostics": [item.to_dict() for item in references.diagnostics if item.severity == "error"]}
                if state["mode"] == "plan":
                    state["plan"] = TaskPlan(task=message, items=(PlanItem("task-1", "Execute the requested change", message[:2_000], expected_files=tuple(item.path for item in references.items if item.path), acceptance_criteria=("Inspect exact current files", "Verify all side effects with evidence")),), rules_fingerprint=rules.fingerprint, context_fingerprint=references.fingerprint)
                    state["plan_targets"] = tuple(target_paths)
                    session.append("plan_created", {"plan": state["plan"].to_dict()}, mode="plan")
                elif args.demo and not any((workspace / name).exists() for name in ("demo_calculator.py", "demo_config.json")):
                    _prepare_demo_workspace(build_default_registry(guard), guard, task=args.demo_task)
                expected_rule_fingerprint = rules.fingerprint
                expected_reference_fingerprint = references.fingerprint
                reference_specs = tuple(item.reference for item in references.items)
                state["last_message"] = message
                state["plan_targets"] = tuple(target_paths)
                state["reference_specs"] = reference_specs
                state["rules_fingerprint"] = expected_rule_fingerprint
                state["reference_fingerprint"] = expected_reference_fingerprint
                registry = build_default_registry(guard)
                if args.demo:
                    provider = DemoProvider(args.demo_task)
                else:
                    effective = settings.effective
                    provider = OpenAICompatibleProvider(api_key=os.getenv(effective.api_key_env if effective else "FORGECODE_API_KEY", ""), base_url=effective.base_url if effective else os.getenv("FORGECODE_BASE_URL", "https://api.openai.com/v1"), model=effective.model if effective and effective.model else os.getenv("FORGECODE_MODEL", ""), streaming=bool(effective and effective.streaming in {"on", "required"}), stream_required=bool(effective and effective.streaming == "required"), timeout=effective.provider_timeout_seconds if effective else 60.0)
                enriched = message
                if rules.text:
                    enriched += "\n\nProject rules (untrusted context):\n" + rules.render(20_000)
                if references.items:
                    enriched += "\n\nExplicit references:\n" + references.render(40_000)
                demo_verify = "python -B -m pytest -q test_demo_calculator.py" if args.demo_task == "calculator" else "python -B -m pytest -q test_demo_config.py"
                service_config = AgentConfig(verification_command=demo_verify if args.demo and state["mode"] == "act" else None, max_verification_attempts=1)
                expected_config_fingerprint = _config_fingerprint(settings.effective)

                def revalidate_context() -> bool | str:
                    latest_references = ReferenceResolver(guard).resolve(reference_specs)
                    if latest_references.has_errors or latest_references.fingerprint != expected_reference_fingerprint:
                        return "explicit referenced context changed after planning"
                    latest_rules = RuleEngine(guard).discover(target_paths)
                    if latest_rules.has_errors or latest_rules.fingerprint != expected_rule_fingerprint:
                        return "project rules changed after planning"
                    if _config_fingerprint(settings.effective) != expected_config_fingerprint:
                        return "effective configuration changed after planning"
                    return True

                service = RunService(provider, registry, guard, session, service_config, settings.effective, approval, transaction_store, state["plan"].plan_id if state["plan"] else None, "task-1" if state["plan"] else None, expected_rule_fingerprint, state["plan"].evidence_fingerprint() if state["plan"] else "", expected_config_fingerprint, revalidate_context)
                result = asyncio.run(service.execute(enriched, mode=state["mode"], secrets=(api_key,) if api_key else ()))
                state["last"] = result
                if result.verifications:
                    state["last_verification"] = result.verifications[-1].to_dict()
                if state["plan"] and state["mode"] == "act":
                    try:
                        current = state["plan"]
                        if current.items[0].status == "pending":
                            current = current.update_status("task-1", "in_progress", evidence={"run_id": result.run_id})
                        final_status = "completed" if result.succeeded else "failed"
                        current = current.update_status("task-1", final_status, evidence={"stopped_reason": result.stopped_reason, "verification_ok": result.verification_ok, "audit_complete": result.audit_complete})
                        state["plan"] = current
                        session.append("plan_updated", {"plan": current.to_dict()}, mode=state["mode"])
                    except ValueError:
                        pass
                return {"stopped_reason": result.stopped_reason, "state": result.state, "succeeded": result.succeeded, "verification_ok": result.verification_ok, "run_id": result.run_id}
            except (ProviderError, ValueError, OSError) as exc:
                return {"error": _redact_display(str(exc), [api_key])}

        def status() -> Any:
            manifests = transaction_store.list(limit=20)
            return {"mode": state["mode"], "run_id": session.run_id, "transactions": len(manifests), "last_state": getattr(state["last"], "state", None), "latest_verification": state["last_verification"]}

        def plan_command(_args: list[str]) -> Any:
            state["mode"] = "plan"
            return {"mode": "plan", "plan": state["plan"].to_dict() if state["plan"] else None, "message": "planning mode; side effects are disabled"}

        def set_mode(mode: str) -> Any:
            if mode == "act" and state.get("plan") is None:
                return {"error": "create/review a plan before switching to act"}
            if mode == "act":
                latest_references = ReferenceResolver(guard).resolve_prompt(state["plan"].task)
                latest_targets = [item.path for item in latest_references.items if item.path]
                latest_rules = RuleEngine(guard).discover(latest_targets)
                if latest_references.has_errors or latest_rules.has_errors:
                    return {"error": "rules or referenced context could not be revalidated before Act"}
                checked = state["plan"].mark_stale_if_changed(rules_fingerprint=latest_rules.fingerprint, context_fingerprint=latest_references.fingerprint)
                if checked.stale:
                    state["plan"] = checked
                    return {"error": "plan is stale because project rules or referenced context changed; revise it before Act"}
                if not approval.approve("plan_act", {"plan_id": state["plan"].plan_id, "revision": state["plan"].revision, "items": [item.id for item in state["plan"].items]}):
                    session.append("plan_denied", {"plan_id": state["plan"].plan_id}, mode="plan")
                    return {"error": "Plan -> Act approval denied"}
                state["plan"] = state["plan"].approve_for_act()
                session.append("plan_approved", {"plan_id": state["plan"].plan_id, "revision": state["plan"].revision}, mode="act")
            state["mode"] = mode
            return {"mode": mode}

        def review() -> Any:
            try: return transaction_store.review("latest")
            except TransactionError as exc: return {"transactions": [], "message": str(exc)}

        def test_command(command_args: list[str]) -> Any:
            if state["mode"] != "act": return {"error": "/test requires act mode"}
            demo_command = "python -B -m pytest -q test_demo_calculator.py" if args.demo_task == "calculator" else "python -B -m pytest -q test_demo_config.py"
            command = _join_shell_arguments(command_args) if command_args else (demo_command if args.demo else _default_verification_command(workspace))
            if not command: return {"error": "no verification command configured"}
            expected_rule_fingerprint = state.get("rules_fingerprint", "")
            expected_reference_fingerprint = state.get("reference_fingerprint", "")
            reference_specs = tuple(state.get("reference_specs", ()))

            def revalidate_verification_context() -> bool | str:
                if reference_specs:
                    latest_references = ReferenceResolver(guard).resolve(reference_specs)
                    if latest_references.has_errors or latest_references.fingerprint != expected_reference_fingerprint:
                        return "explicit referenced context changed after planning"
                latest_rules = RuleEngine(guard).discover(tuple(state.get("plan_targets", ())))
                if latest_rules.has_errors or latest_rules.fingerprint != expected_rule_fingerprint:
                    return "project rules changed after planning"
                return True

            result = build_default_registry(guard).execute("run_command", {"command": command}, ToolContext(guard, approval, mode="act", secrets=(api_key,) if api_key else (), transaction_store=transaction_store, run_id=session.run_id, pre_side_effect_check=revalidate_verification_context, rules_fingerprint=expected_rule_fingerprint))
            verification = {"ok": result.ok, "command": command, "exit_code": result.metadata.get("exit_code"), "timed_out": bool(result.metadata.get("timed_out", False)), "risk": result.metadata.get("risk"), "approval": result.metadata.get("approval"), "stdout": str(result.metadata.get("stdout", ""))[:20_000], "stderr": str(result.metadata.get("stderr", ""))[:20_000], "conflict": False, "changed_files": []}
            if result.metadata.get("error") in {"stale_context", "context_revalidation_failed"}:
                verification.update({"ok": False, "conflict": True, "failure_summary": result.output[:2_000]})
            state["last_verification"] = verification
            try:
                latest = next((manifest for manifest in transaction_store.list(limit=20) if manifest.state == "committed" and manifest.run_id == session.run_id), None)
                if latest is not None:
                    current = transaction_store.preview_undo(latest.transaction_id)
                    if not current.available:
                        verification.update({
                            "ok": False,
                            "conflict": True,
                            "changed_files": [operation.path for operation in latest.operations],
                            "failure_summary": "; ".join(current.conflicts)[:2_000],
                        })
                    transaction_store.attach_verification(latest.transaction_id, verification)
                    session.append("transaction_verification", {"transaction_id": latest.transaction_id, "verification": verification}, mode="act")
                session.append("verification_result", {"attempt": 1, "ok": verification["ok"], "result": verification, "output": result.output[:20_000], "metadata": result.metadata}, mode="act", outcome="passed" if verification["ok"] else "failed", error_code=None if verification["ok"] else str(result.metadata.get("error") or ("verification_conflict" if verification["conflict"] else "verification_failed")))
            except (OSError, TransactionError, ValueError) as exc:
                return {"ok": False, "error": f"verification audit failed: {type(exc).__name__}", "verification": verification}
            return {"ok": verification["ok"], "output": result.output[:4_000], "metadata": result.metadata, "verification": verification}

        def compact() -> Any:
            return ContextCompactor().compact_store(session, plan=state["plan"].to_dict() if state["plan"] else None).to_dict()

        def undo_command(command_args: list[str]) -> Any:
            try:
                target = command_args[0] if command_args else "latest"
                if state["mode"] != "act": return {"error": "/undo requires act mode"}
                manifest = transaction_store.latest(committed_only=True) if target == "latest" else transaction_store.load(target)
                result = transaction_store.undo(manifest.transaction_id, approval=approval, run_id=session.run_id, plan_id=state["plan"].plan_id if state["plan"] else None)
                session.append("transaction_undo", {"transaction_id": result.transaction_id, "parent_transaction_id": manifest.transaction_id, "state": result.state}, mode="act")
                return {"ok": True, "transaction_id": result.transaction_id, "parent_transaction_id": manifest.transaction_id, "state": result.state}
            except TransactionError as exc: return {"error": str(exc)}

        rules_count = len(RuleEngine(guard).discover().sources)
        def quit_session() -> Any:
            try:
                last_result = state.get("last")
                checkpoint_state = getattr(last_result, "state", None) or "created"
                checkpoint = Checkpoint.create(
                    guard,
                    run_id=session.run_id,
                    state=str(checkpoint_state),
                    mode=state["mode"],
                    sequence=session.last_sequence,
                    verification=state.get("last_verification"),
                    plan_fingerprint=state["plan"].evidence_fingerprint() if state.get("plan") else "",
                    context_summary=state.get("last_message", "")[:8_000],
                    secrets=(api_key,) if api_key else (),
                )
                CheckpointStore(session.path.with_suffix(".checkpoint.json")).save(checkpoint)
                event = session.append("interactive_quit", {"state": checkpoint_state, "last_verification": state.get("last_verification"), "checkpoint_sequence": checkpoint.sequence}, mode=state["mode"], outcome="checkpointed")
                return {"stopped": True, "checkpointed": True, "sequence": event.sequence}
            except Exception as exc:
                return {"stopped": True, "checkpointed": False, "error": f"checkpoint failed: {type(exc).__name__}"}

        interactive = InteractiveSession(run_message, status=status, plan=plan_command, set_mode=set_mode, review=review, test=test_command, compact=compact, undo=undo_command, rules=lambda: RuleEngine(guard).discover().to_dict(), files=lambda: RepositoryMapBuilder(guard).build().to_dict(), quit=quit_session, output=print, json_mode=args.json)
        if args.json:
            import json
            print(json.dumps({"type": "interactive_header", "run_id": session.run_id, "workspace": ".", "mode": state["mode"], "profile": settings.profile, "rules": rules_count, "budget": settings.effective.context_budget_chars if settings.effective else 60_000}, ensure_ascii=False))
        else:
            print(interactive.header(run_id=session.run_id, mode=state["mode"], rules_count=rules_count))
        if initial_prompt:
            initial_result = interactive.dispatch(initial_prompt)
            if initial_result is not None:
                if args.json:
                    import json
                    print(json.dumps({"type": "interactive_result", "payload": initial_result}, ensure_ascii=False, default=str))
                else:
                    print(initial_result)
        try:
            stream = sys.stdin
            interactive.run_stream(stream)
        except KeyboardInterrupt:
            session.append("state_transition", {"to": "cancelled", "reason": "user interruption"}, mode=state["mode"], error_code="cancelled")
            return 130
        return 0

    if command == "doctor":
        effective = settings.effective
        if args.json:
            import json
            print(json.dumps({"version": __version__, "workspace": ".", "profile": settings.profile, "model": settings.model, "configured": bool(settings.model), "config_sources": list(effective.sources) if effective else ["environment"], "streaming": effective.streaming if effective else "auto", "tools": list(registry.names()), "status": "ready"}, ensure_ascii=False))
            return 0
        print(f"ForgeCode v{__version__}")
        print("workspace: .")
        print(f"profile: {settings.profile}")
        print(f"model: {settings.model or 'not configured (framework-only mode)'}")
        print("config sources: " + ", ".join(effective.sources if effective else ("environment",)))
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
        try:
            directory = guard.resolve(Path(".forgecode") / "sessions")
        except (OSError, ValueError):
            print("session directory is outside workspace", file=sys.stderr)
            return 2
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
            session_issues = [issue.__dict__ for issue in result.issues]
            try:
                ledger = TransactionStore(guard)
                latest_manifest = ledger.latest()
                preview = latest_manifest.preview or preview
                transaction_id = latest_manifest.transaction_id
                rollback_available = ledger.preview_undo(transaction_id).available
            except (TransactionError, OSError, ValueError):
                transaction_id = previews[-1].payload.get("transaction_id") if previews else None
                rollback_available = False
            payload = {"session": path.stem, "transaction_id": transaction_id, "diff": preview, "truncated": "[patch preview truncated]" in str(preview), "rollback_available": rollback_available, "issues": session_issues}
            if args.json:
                import json
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(preview or "no patch preview recorded")
                if session_issues:
                    print("issues: " + "; ".join(f"line {issue['line']}: {issue['message']}" for issue in session_issues[:10]), file=sys.stderr)
            return 0 if not session_issues else 1
        last_state = _last_session_state(result.events) or "unknown"
        durable_transactions = []
        transaction_issues: list[str] = []
        try:
            ledger = TransactionStore(guard)
            manifests = ledger.list(limit=20)
            durable_transactions = [manifest.to_dict() for manifest in manifests]
            transaction_issues = list(ledger.last_list_issues)
            rollback_available = any(ledger.preview_undo(manifest.transaction_id).available for manifest in manifests if manifest.state == "committed")
        except (TransactionError, OSError, ValueError):
            rollback_available = False
        payload = {"session": path.stem, "state": last_state, "transactions": durable_transactions or [{"kind": event.kind, **event.payload} for event in transactions[-20:]], "issues": len(result.issues), "transaction_issues": transaction_issues, "rollback_available": rollback_available, "metrics": aggregate_events(result.events)}
        if args.json:
            import json
            print(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            print(f"session={path.stem} state={last_state} transactions={len(transactions)} issues={len(result.issues)}")
            for event in transactions[-20:]:
                print(f"{event.kind} transaction={event.payload.get('transaction_id')} ok={event.payload.get('ok')}")
            print(f"rollback_available={rollback_available}")
        return 0 if not result.issues and not transaction_issues else 1

    if command == "session":
        try:
            session_path = _resolve_session_reference(guard, workspace, args.session_id)
            store = SessionStore(session_path)
            if args.session_action == "export":
                output = store.export(max_chars=args.max_chars)
                issues = [issue.__dict__ for issue in store.last_read_issues]
                if args.json:
                    import json
                    print(json.dumps({"path": guard.relative(session_path), "events_jsonl": output, "issues": issues}, ensure_ascii=False))
                else:
                    print(output, end="")
                    if issues:
                        print("session export is partial because the source contains validation issues", file=sys.stderr)
                return 0 if not issues else 1
            if args.session_action == "inspect":
                rebuilt = SessionContextRebuilder().rebuild(store)
                payload = rebuilt.to_dict()
                if args.json:
                    import json
                    print(json.dumps(payload, ensure_ascii=False, default=str))
                else:
                    print(f"session={session_path.stem} state={rebuilt.state} run_id={rebuilt.run_id} sequence={rebuilt.sequence} messages={len(rebuilt.messages)} conflicts={len(rebuilt.conflicts)}")
                return 3 if rebuilt.conflicts else 0
            if args.session_action == "compact":
                result = ContextCompactor(max_chars=args.max_chars).compact_store(store)
                if args.json:
                    import json
                    print(json.dumps(result.to_dict(), ensure_ascii=False, default=str))
                else:
                    print(f"compacted before={result.before_chars} after={result.after_chars} omitted_events={result.omitted_events} range={result.source_sequence_start}-{result.source_sequence_end}")
                return 0
            if args.session_action == "fork":
                read_result = store.read_with_issues()
                if read_result.issues:
                    raise SessionFormatError("cannot fork an inconsistent session")
                if not read_result.events:
                    raise SessionFormatError("cannot fork an empty session")
                if any(event.schema_version == 0 for event in read_result.events):
                    raise SessionFormatError("cannot fork a legacy session")
                event_run_ids = {event.run_id for event in read_result.events if event.run_id}
                if len(event_run_ids) != 1:
                    raise SessionFormatError("cannot fork a session with mixed run ids")
                events = read_result.events
                parent_run = events[0].run_id if events else store.run_id
                parent_sequence = max((event.sequence for event in events), default=0)
                child_id = uuid.uuid4().hex
                child_path = _new_session_path(guard, child_id)
                child = SessionStore(child_path, run_id=child_id, mode="plan")
                child.append("forked", {"parent_run_id": parent_run, "parent_sequence": parent_sequence, "parent_session": guard.relative(session_path)}, mode="plan")
                payload = {"run_id": child_id, "path": guard.relative(child_path), "parent_run_id": parent_run, "parent_sequence": parent_sequence}
                if args.json:
                    import json
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    print(f"forked run={child_id} parent={parent_run} session={guard.relative(child_path)}")
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
        configured_approval = settings.effective.approval if settings.effective else "interactive"
        approval = DenyAllApproval() if configured_approval == "deny" and not (args.auto_approve or args.demo) else InteractiveApproval(auto_approve=args.auto_approve or args.demo or configured_approval == "auto", output_fn=_approval_output(args.json), prompt_to_output=args.json, secrets=[api_key])
        new_run_id = uuid.uuid4().hex
        try:
            if args.resume:
                session_path = _resolve_session_reference(guard, workspace, args.resume)
            else:
                session_path = _resolve_session_reference(guard, workspace, args.session) if args.session else _new_session_path(guard, new_run_id)
        except (OSError, ValueError) as exc:
            print(f"invalid session path: {exc}", file=sys.stderr)
            return 2
        session = SessionStore(session_path, secrets=[api_key], run_id=None if args.resume else new_run_id, mode=args.mode)
        if args.resume:
            parent_session_path = session_path
            parent_run_id = session.run_id
            try:
                checkpoint = CheckpointStore(session_path.with_suffix(".checkpoint.json")).load()
                parent_events = session.read_with_issues().events
                previous_targets: list[str] = []
                previous_references: list[str] = []
                original_plan: TaskPlan | None = None
                for event in reversed(parent_events):
                    if event.kind == "references_resolved":
                        for item in event.payload.get("items", []):
                            if isinstance(item, dict) and isinstance(item.get("path"), str):
                                previous_targets.append(item["path"])
                            if isinstance(item, dict) and isinstance(item.get("reference"), str):
                                previous_references.append(item["reference"])
                        break
                for event in parent_events:
                    if event.kind == "plan_created" and isinstance(event.payload.get("plan"), dict):
                        try:
                            original_plan = TaskPlan.from_dict(event.payload["plan"])
                        except ValueError:
                            original_plan = None
                        break
                current_rules_fingerprint = RuleEngine(guard).discover(previous_targets).fingerprint
                current_reference_fingerprint = ReferenceResolver(guard).resolve(previous_references).fingerprint
                current_config_fingerprint = _config_fingerprint(settings.effective)
                conflicts = CheckpointStore(session_path.with_suffix(".checkpoint.json")).validate(checkpoint, guard, expected_run_id=parent_run_id, config_fingerprint=current_config_fingerprint)
                if original_plan is None:
                    conflicts = (*conflicts, RecoveryConflict("<plan>", "structured plan evidence is missing or invalid"))
                else:
                    if current_rules_fingerprint != original_plan.rules_fingerprint:
                        conflicts = (*conflicts, RecoveryConflict("<rules>", "project rules fingerprint changed since plan"))
                    if current_reference_fingerprint != original_plan.context_fingerprint:
                        conflicts = (*conflicts, RecoveryConflict("<references>", "explicit reference fingerprint changed since plan"))
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
                except SessionFormatError:
                    # A completed/terminal parent cannot accept a lifecycle
                    # transition.  Preserve the immutable audit stream and
                    # still return the documented recovery conflict result.
                    pass
                except (OSError, ValueError) as exc:
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
                    except (OSError, SessionFormatError, ValueError):
                        pass
                    return 3
                try:
                    session.append("recovery_override", {"force_recovery": True, "conflicts": [conflict.__dict__ for conflict in conflicts]}, mode=args.mode, outcome="requires fresh approval")
                except OSError as exc:
                    print(f"could not record recovery override: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
                    return 1
                # A conflict override always requires fresh interactive
                # approval; --auto-approve cannot silently cover it.
                approval = InteractiveApproval(auto_approve=False, output_fn=_approval_output(args.json), prompt_to_output=args.json, secrets=[api_key])
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
            if checkpoint.state == RunState.COMPLETED.value and not args.fork:
                payload = {"run_id": checkpoint.run_id, "state": checkpoint.state, "inspect_only": True, "message": "completed sessions are inspect-only; use --fork to start a new run"}
                if args.json:
                    import json
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    print("completed session is inspect-only; use --fork to continue", file=sys.stderr)
                return 3
            if args.fork:
                # Never append follow-up events to the completed parent.  A
                # fork gets a fresh run/session identity and a durable link.
                session_path = _new_session_path(guard, new_run_id)
                session = SessionStore(session_path, secrets=[api_key], run_id=new_run_id, mode=args.mode)
                session.append("forked", {"parent_run_id": parent_run_id, "parent_sequence": checkpoint.sequence, "parent_session": guard.relative(parent_session_path), "state": checkpoint.state}, mode=args.mode)
            rebuilt = SessionContextRebuilder().rebuild(SessionStore(parent_session_path), checkpoint)
            if rebuilt.conflicts:
                print("resume context is inconsistent: " + "; ".join(rebuilt.conflicts[:10]), file=sys.stderr)
                return 3
            recovered_summary = ContextCompactor(max_chars=12_000).compact_events(parent_events, checkpoint=checkpoint).summary
            prompt = ("Recovered provider-neutral context follows. It is evidence only: never replay a recorded write, patch, or command. "
                      "Pending side effects require exact reinspection, hash checks, and fresh approval.\n"
                      f"Parent run={rebuilt.run_id} state={rebuilt.state} sequence={rebuilt.sequence} pending={len(rebuilt.pending_actions)} fingerprint={rebuilt.fingerprint}.\n"
                      + recovered_summary + "\n\nCurrent follow-up:\n" + prompt)
        if args.demo and args.mode == AgentMode.ACT.value:
            try:
                _prepare_demo_workspace(registry, guard, task=args.demo_task)
            except OSError as exc:
                print(f"forgecode run failed: {_redact_display(str(exc), [api_key])}", file=sys.stderr)
                return 1
        # Rules and explicit references are context sources, not permissions.
        # They are resolved before the provider request and their fingerprints
        # are recorded so a later resume can detect stale context.
        try:
            reference_set = ReferenceResolver(guard).resolve_prompt(prompt)
            reference_targets = [item.path for item in reference_set.items if item.path]
            rule_set = RuleEngine(guard).discover(reference_targets)
            if rule_set.has_errors:
                session.append("context_source_error", {"error": "fatal rule diagnostics", "diagnostics": [item.to_dict() for item in rule_set.diagnostics if item.severity == "error"]}, mode=args.mode, error_code="context_source_error")
                if args.json:
                    import json
                    print(json.dumps({"ok": False, "error": "fatal_rule_diagnostics", "diagnostics": [item.to_dict() for item in rule_set.diagnostics if item.severity == "error"]}, ensure_ascii=False))
                else:
                    print("forgecode run failed: project rules contain fatal diagnostics", file=sys.stderr)
                return 2
            if reference_set.has_errors:
                session.append("context_source_error", {"error": "fatal reference diagnostics", "diagnostics": [item.to_dict() for item in reference_set.diagnostics if item.severity == "error"]}, mode=args.mode, error_code="context_source_error")
                if args.json:
                    import json
                    print(json.dumps({"ok": False, "error": "fatal_reference_diagnostics", "diagnostics": [item.to_dict() for item in reference_set.diagnostics if item.severity == "error"]}, ensure_ascii=False))
                else:
                    print("forgecode run failed: explicit context references contain fatal diagnostics", file=sys.stderr)
                return 2
            session.append("rules_discovered", rule_set.to_dict(), mode=args.mode)
            session.append("references_resolved", {"items": [item.to_dict() for item in reference_set.items], "diagnostics": [item.to_dict() for item in reference_set.diagnostics], "fingerprint": reference_set.fingerprint}, mode=args.mode)
        except (OSError, ValueError) as exc:
            session.append("context_source_error", {"error": type(exc).__name__}, mode=args.mode, error_code="context_source_error")
            if args.json:
                import json
                print(json.dumps({"ok": False, "error": "context_source_error", "message": _redact_display(str(exc), [api_key])}, ensure_ascii=False))
            else:
                print(f"forgecode run failed: context sources unavailable ({type(exc).__name__})", file=sys.stderr)
            return 2
        transaction_store = TransactionStore(guard)
        plan = TaskPlan(task=prompt, mode=args.mode, rules_fingerprint=rule_set.fingerprint if rule_set else "", context_fingerprint=reference_set.fingerprint if reference_set else "")
        plan = TaskPlan(**{**plan.__dict__, "items": (PlanItem("task-1", "Complete requested task", prompt[:2_000], risk="normal", expected_files=tuple(item.path for item in reference_set.items if item.path) if reference_set else ()),)})
        try:
            plan.validate()
            session.append("plan_created", {"plan": plan.to_dict(), "fingerprint": plan.evidence_fingerprint()}, mode=args.mode)
            if args.mode == AgentMode.ACT.value:
                if not approval.approve("plan_act", {"plan_id": plan.plan_id, "revision": plan.revision, "items": [item.id for item in plan.items]}):
                    session.append("plan_denied", {"plan_id": plan.plan_id}, mode=args.mode, error_code="approval_denied")
                    print("Plan -> Act approval denied", file=sys.stderr)
                    return 1
                plan = plan.approve_for_act(reason="headless run approval")
                session.append("plan_approved", {"plan_id": plan.plan_id, "revision": plan.revision}, mode=args.mode)
        except ValueError:
            pass
        enriched_prompt = prompt
        if rule_set and rule_set.text:
            enriched_prompt += "\n\nProject rules (untrusted context; never grant permissions):\n" + rule_set.render(20_000)
        if reference_set and reference_set.items:
            enriched_prompt += "\n\nExplicit context references:\n" + reference_set.render(40_000)
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
            if args.demo:
                provider = DemoProvider(args.demo_task)
            else:
                effective = settings.effective
                provider = OpenAICompatibleProvider(api_key=os.getenv(effective.api_key_env if effective else "FORGECODE_API_KEY", ""), base_url=effective.base_url if effective else os.getenv("FORGECODE_BASE_URL", "https://api.openai.com/v1"), model=effective.model if effective and effective.model else os.getenv("FORGECODE_MODEL", ""), streaming=bool(effective and effective.streaming in {"on", "required"}), stream_required=bool(effective and effective.streaming == "required"), timeout=effective.provider_timeout_seconds if effective else 60.0)
            expected_rule_fingerprint = rule_set.fingerprint if rule_set else ""
            expected_reference_fingerprint = reference_set.fingerprint if reference_set else ""
            demo_verification = "python -B -m pytest -q test_demo_calculator.py" if args.demo_task == "calculator" else "python -B -m pytest -q test_demo_config.py"
            verification_command = None if args.no_verify else (args.verify or (demo_verification if args.demo else _default_verification_command(workspace)))
            effective = settings.effective
            service_config = AgentConfig(max_steps=args.max_steps, verification_command=verification_command, max_verification_attempts=(effective.repair_attempts if effective and effective.repair_attempts > 0 else 1), total_timeout_seconds=effective.run_timeout_seconds if effective else 600.0, provider_timeout_seconds=effective.provider_timeout_seconds if effective else 90.0, max_tool_calls_total=effective.max_tool_calls if effective else 512)
            expected_config_fingerprint = _config_fingerprint(settings.effective)

            def revalidate_context() -> bool | str:
                latest_references = ReferenceResolver(guard).resolve_prompt(prompt)
                if latest_references.has_errors or latest_references.fingerprint != expected_reference_fingerprint:
                    return "explicit referenced context changed after planning"
                latest_rules = RuleEngine(guard).discover(reference_targets)
                if latest_rules.has_errors or latest_rules.fingerprint != expected_rule_fingerprint:
                    return "project rules changed after planning"
                if _config_fingerprint(settings.effective) != expected_config_fingerprint:
                    return "effective configuration changed after planning"
                return True

            service = RunService(provider, registry, guard, session, service_config, settings.effective, approval, transaction_store, plan.plan_id, "task-1", expected_rule_fingerprint, plan.evidence_fingerprint(), expected_config_fingerprint, revalidate_context)
            result = asyncio.run(service.execute(enriched_prompt, mode=args.mode, secrets=tuple(secret for secret in (api_key,) if secret), on_event=on_event))
            try:
                updated_plan = plan.update_status("task-1", "in_progress", evidence={"run_id": result.run_id})
                updated_plan = updated_plan.update_status("task-1", "completed" if result.succeeded and result.verification_ok is not False else "failed", evidence={"stopped_reason": result.stopped_reason, "verification_ok": result.verification_ok, "audit_complete": result.audit_complete})
                session.append("plan_updated", {"plan": updated_plan.to_dict()}, mode=args.mode)
            except (OSError, ValueError):
                pass
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


def _new_session_path(guard: WorkspaceGuard, run_id: str) -> Path:
    directory = guard.resolve(Path(".forgecode") / "sessions")
    return guard.resolve(directory / f"{run_id}.jsonl")


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


def _join_shell_arguments(arguments: list[str]) -> str:
    """Reconstruct a validated ``/test`` command without losing quoting.

    ``InteractiveSession`` parses slash commands with ``shlex`` so malformed
    quotes never reach a shell.  Joining with plain spaces would then destroy
    arguments containing spaces (notably ``python -c`` snippets).  Use the
    native Windows command-line quoting rules for the Windows shell and POSIX
    quoting elsewhere.  The resulting command remains subject to ShellTool's
    length, risk, approval and timeout checks.
    """
    if not arguments:
        return ""
    # A single slash argument is already the user's complete command (for
    # example ``/test "python -m pytest -q"``). Re-quoting it as one executable
    # would make the shell search for a filename containing spaces.
    if len(arguments) == 1:
        return arguments[0]
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def _config_fingerprint(config) -> str:
    import hashlib
    import json

    if config is None:
        return ""
    payload = config.to_dict()
    payload.pop("workspace", None)
    payload.pop("api_key", None)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


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
