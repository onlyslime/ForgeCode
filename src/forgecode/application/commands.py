"""Command-line entry point for the framework skeleton."""

import argparse
import asyncio
from dataclasses import asdict, replace
import json
import json as jsonlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import re
import difflib
import time
import threading
import queue
import uuid
import hashlib as hashlib_lib
import urllib.request
import urllib.error
from typing import Any

from .. import __version__
from ..agent import AgentConfig, AgentLoop, ContextCompactor, RunState, SessionContextRebuilder
from .interactive_service import CommandShortcut, InteractiveRunController, InteractiveSession, ShortcutParseError, _human_result, _interactive_error_code, _interactive_success, parse_command_shortcut
from .run_service import RunService
from .session_service import aggregate_events
from ..context import ContextIndex, ContextIndexError, RepositoryMapBuilder
from ..skills import MAX_SKILL_INPUT_CHARS, SkillError, SkillExecutor, SkillInvocation, SkillLoader, SkillRegistry
from ..config import ConfigError, ConfigLoader, Settings, parse_tool_policy_options, SUPPORTED_PROVIDERS, PROVIDER_CATALOG, provider_metadata, provider_requires_credential
from ..references import ReferenceResolver, parse_references
from ..rules import RuleEngine
from ..plan import PlanItem, TaskPlan
from ..storage import TransactionError, TransactionStore
from ..models import CancellationToken, DemoProvider, OpenAICompatibleProvider, ProviderError, create_provider
from ..testing import TestProfileError, TestProfileLoader, TestProfileRunner
from ..review import ReviewArtifactError, ReviewError
from ..evaluation import evaluate_session
from ..security.redaction import redact_text, redact_value
from ..security.json import bounded_json_loads
from ..security.workspace import WorkspaceGuard
from ..security.trust import TrustError, TrustStore
from ..telemetry import Telemetry
from ..storage import Checkpoint, CheckpointStore, RecoveryConflict, SessionFormatError, SessionStore
from ..tools import AgentMode, AllowAllApproval, DenyAllApproval, InteractiveApproval, ToolContext, build_default_registry
from ..hooks import Hook, HookRegistry
from .review_service import ReviewService
from .prompt_ui import run_prompt_ui


def _load_saved_credential(workspace: Path) -> None:
    """Load a workspace-local ignored credential when no environment value exists."""
    path = workspace / ".forgecode" / "credentials.json"
    try:
        if path.is_file() and not os.getenv("FORGECODE_API_KEY"):
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("FORGECODE_API_KEY") if isinstance(data, dict) else None
            if isinstance(value, str) and value:
                os.environ["FORGECODE_API_KEY"] = value
    except (OSError, ValueError):
        pass


def _save_connection(workspace: Path, *, endpoint: str, model: str, api_key: str) -> None:
    """Persist connection metadata and credential in ignored workspace state."""
    folder = workspace / ".forgecode"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "credentials.json").write_text(json.dumps({"FORGECODE_API_KEY": api_key}, ensure_ascii=False), encoding="utf-8")
    (folder / "config.toml").write_text("provider = \"openai-compatible\"\n" + f"base_url = {json.dumps(endpoint)}\nmodel = {json.dumps(model)}\n", encoding="utf-8")


def _build_provider(effective):
    """Construct the selected provider while preserving legacy injection."""
    provider_name = effective.provider if effective else "openai-compatible"
    kwargs = dict(
        api_key=os.getenv(effective.api_key_env if effective else "FORGECODE_API_KEY", ""),
        base_url=effective.base_url if effective else os.getenv("FORGECODE_BASE_URL", "https://api.openai.com/v1"),
        model=effective.model if effective and effective.model else os.getenv("FORGECODE_MODEL", ""),
        # Human chat defaults to SSE when the provider supports it; ``auto``
        # keeps compatibility by falling back to a normal completion if the
        # transport has no streaming implementation.
        streaming=bool(effective and effective.streaming in {"auto", "on", "required"}),
        stream_required=bool(effective and effective.streaming == "required"),
        timeout=effective.provider_timeout_seconds if effective else 60.0,
    )
    if provider_name == "openai-compatible":
        return OpenAICompatibleProvider(**kwargs)
    return create_provider(provider=provider_name, **kwargs)


def _approval_output(json_mode: bool):
    """Keep interactive approval prompts off machine-readable stdout."""
    return (lambda message: print(message, file=sys.stderr)) if json_mode else print


def _provider_probe(base_url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    """Perform a minimal opt-in, credential-free reachability probe."""
    request = urllib.request.Request(base_url.rstrip("/") + "/", method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=max(0.5, min(timeout, 5.0))) as response:
            return {"performed": True, "reachable": True, "status": int(response.status)}
    except urllib.error.HTTPError as exc:
        return {"performed": True, "reachable": True, "status": int(exc.code), "category": "http_error"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"performed": True, "reachable": False, "category": "network_error", "error": str(exc)[:200]}


# Machine-facing command output is deliberately kept independent from the
# durable session/event schemas.  Every command that advertises ``--json`` or
# ``--jsonl`` can use this small renderer to guarantee the same required
# fields, while retaining room for command-specific data.  ``jsonl`` is a
# transport spelling here: one invocation emits one envelope line (progress
# and approval diagnostics remain on stderr).
CLI_SCHEMA_VERSION = 1
MAX_RECOVERY_PROMPT_CHARS = 8_000
MAX_RECOVERY_FOLLOWUP_CHARS = 2_000

# These keys define the wire-level envelope and must never be replaced by a
# compatibility alias.  Older command clients are intentionally supported by
# additive fields (for example ``report`` or ``type``), but a caller must
# always be able to trust the canonical schema keys.
_MACHINE_CANONICAL_KEYS = frozenset({"schema_version", "kind", "ok", "command", "data", "error", "exit_code"})


def _compat_aliases(values: dict[str, Any] | None) -> dict[str, Any]:
    """Return safe additive aliases without allowing schema shadowing."""
    if not values:
        return {}
    return {key: value for key, value in values.items() if key not in _MACHINE_CANONICAL_KEYS}


def _machine_envelope(
    command: str,
    kind: str,
    ok: bool,
    *,
    data: Any | None = None,
    error: dict[str, Any] | None = None,
    exit_code: int | None = None,
    **compat: Any,
) -> dict[str, Any]:
    """Build a bounded, stable machine-output envelope.

    Successful responses always contain a ``data`` object (an empty object is
    used when a command has no additional result).  Failed responses contain
    an ``error`` object with a short code/message instead.  Compatibility
    aliases may be supplied as additive top-level fields for older clients;
    the canonical payload remains under ``data``/``error``.
    """
    payload: dict[str, Any] = {
        "schema_version": CLI_SCHEMA_VERSION,
        "kind": str(kind),
        "ok": bool(ok),
        "command": str(command),
    }
    if ok:
        payload["data"] = data if isinstance(data, dict) else ({} if data is None else {"value": data})
    else:
        if error is None:
            error = {"code": "command_failed", "message": "command failed"}
        payload["error"] = error
    if exit_code is not None:
        payload["exit_code"] = int(exit_code)
    # ``compat`` is deliberately additive.  In particular, an old payload
    # containing ``ok``/``data``/``error`` must not be able to turn a failed
    # envelope into a superficially successful one (or vice versa).
    payload.update(_compat_aliases(compat))
    return payload


def _emit_machine(payload: dict[str, Any]) -> None:
    """Emit exactly one UTF-8 JSON document on stdout.

    ``allow_nan=False`` prevents non-finite values from silently producing
    output that strict JSON consumers cannot parse.  Callers are responsible
    for bounding command-specific data before handing it to this function.
    """
    if not isinstance(payload, dict):
        payload = _machine_error("unknown", "invalid_envelope", "machine output must be an object", exit_code=2)
    else:
        # Last-mile normalization protects the invariant even when a legacy
        # branch adds aliases with ``dict.update`` after constructing an
        # envelope.  Canonical fields always win and data/error are exclusive.
        payload = dict(payload)
        payload["schema_version"] = CLI_SCHEMA_VERSION
        payload["kind"] = str(payload.get("kind") or ("result" if payload.get("ok") else "error"))[:128]
        payload["command"] = str(payload.get("command") or "unknown")[:128]
        payload["ok"] = bool(payload.get("ok", False))
        if payload["ok"]:
            payload.pop("error", None)
            data = payload.get("data")
            payload["data"] = data if isinstance(data, dict) else ({} if data is None else {"value": data})
        else:
            payload.pop("data", None)
            error = payload.get("error")
            if not isinstance(error, dict):
                error = {"code": "command_failed", "message": str(error or "command failed")[:2_000]}
            else:
                error = dict(error)
                error["code"] = str(error.get("code") or "command_failed")[:128]
                error["message"] = str(error.get("message") or "command failed")[:2_000]
            payload["error"] = error
        if "exit_code" in payload:
            try:
                payload["exit_code"] = int(payload["exit_code"])
            except (TypeError, ValueError, OverflowError):
                payload.pop("exit_code", None)
    print(jsonlib.dumps(payload, ensure_ascii=False, default=str, allow_nan=False))


def _machine_error(command: str, code: str, message: str, *, exit_code: int | None = None, details: Any | None = None) -> dict[str, Any]:
    """Return a standard error envelope with an optional bounded details map."""
    error: dict[str, Any] = {"code": str(code), "message": str(message)[:2_000]}
    if details is not None:
        error["details"] = details
    return _machine_envelope(command, "error", False, error=error, exit_code=exit_code)


def _fit_recovery_text(value: str, limit: int) -> str:
    """Keep recovered evidence bounded while preserving a clear marker."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    marker = "\n[recovered context truncated]"
    if len(marker) >= limit:
        return marker[:limit]
    return text[: limit - len(marker)] + marker


def _build_recovery_prompt(summary: str, follow_up: str) -> str:
    """Compose a plan-valid recovery prompt without dropping the follow-up."""
    prefix = (
        "Recovered provider-neutral context follows. It is evidence only: "
        "never replay a recorded write, patch, or command. Pending side "
        "effects require exact reinspection, hash checks, and fresh approval.\n"
    )
    follow = _fit_recovery_text(follow_up, MAX_RECOVERY_FOLLOWUP_CHARS)
    suffix = "\n\nCurrent follow-up:\n" + follow
    summary_budget = max(0, MAX_RECOVERY_PROMPT_CHARS - len(prefix) - len(suffix))
    recovered = _fit_recovery_text(summary, summary_budget)
    result = prefix + recovered + suffix
    # The plan schema has an 8,000-character task bound.  This final guard is
    # intentionally defensive in case constants or header text change later.
    if len(result) > MAX_RECOVERY_PROMPT_CHARS:
        result = result[:MAX_RECOVERY_PROMPT_CHARS]
    return result


def _emit_legacy_error(command: str, code: str, message: str, *, exit_code: int, **aliases: Any) -> None:
    """Emit the pre-v0.0.8 error shape for explicitly legacy JSON callers.

    The strict envelope is the default for new machine integrations and all
    JSONL output.  A small compatibility escape hatch remains for the old
    ``run --json``/``chat --json`` clients that consumed ``error`` as a
    string.  It is selected only from the original argv spelling by ``main``;
    no new command silently downgrades its protocol.
    """
    payload: dict[str, Any] = {
        "type": "error",
        "ok": False,
        "error": str(code)[:128],
        "message": str(message)[:2_000],
        "exit_code": int(exit_code),
    }
    for key, value in aliases.items():
        if key not in {"schema_version", "kind", "data", "error"}:
            payload[key] = value
    print(jsonlib.dumps(payload, ensure_ascii=False, default=str, allow_nan=False))


def _emit_command_payload(
    args: argparse.Namespace,
    command: str,
    kind: str,
    payload: Any,
    *,
    ok: bool = True,
    exit_code: int = 0,
    aliases: bool = True,
    type_label: str | None = None,
) -> None:
    """Render a command payload according to the JSON/JSONL compatibility rules.

    ``--json`` retains historical top-level shapes for existing scripts.  The
    newer ``--jsonl`` spelling always emits the canonical envelope.  Additive
    aliases are copied into that envelope when the legacy payload is a map,
    allowing gradual migration without weakening the schema contract.
    """
    if not (getattr(args, "jsonl", False) or getattr(args, "json", False)):
        return
    if ok:
        envelope = _machine_envelope(command, kind, True, data=payload, exit_code=exit_code)
        if aliases and isinstance(payload, dict):
            envelope.update(_compat_aliases(payload))
    else:
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
        elif isinstance(payload, dict):
            error = {"code": str(payload.get("error", "command_failed")), "message": str(payload.get("message", payload.get("error", "command failed")))[:2_000]}
        else:
            error = {"code": "command_failed", "message": str(payload)[:2_000]}
        envelope = _machine_envelope(command, "error", False, error=error, exit_code=exit_code)
        if aliases and isinstance(payload, dict):
            envelope.update(_compat_aliases({key: value for key, value in payload.items() if key not in {"data", "error"}}))
    if type_label:
        envelope["type"] = type_label
    _emit_machine(envelope)


def _add_tool_policy_arguments(parser: argparse.ArgumentParser) -> None:
    """Add Pi-inspired runtime tool narrowing options to a command parser."""
    parser.add_argument(
        "--tools",
        help="comma-separated allowlist of built-in tools (can only narrow configured policy)",
    )
    parser.add_argument(
        "--exclude-tools",
        help="comma-separated built-in tools to disable (can only narrow configured policy)",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="disable all model and verification tools for this run",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgecode", description="Self-built coding agent framework")
    parser.add_argument("--version", action="version", version=f"forgecode {__version__}")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="workspace root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON where supported")
    parser.add_argument("--jsonl", action="store_true", help="emit one machine-readable JSON envelope line")
    subparsers = parser.add_subparsers(dest="command")
    doctor_parser = subparsers.add_parser("doctor", help="check the local framework setup")
    doctor_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    doctor_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    tools_parser = subparsers.add_parser("tools", help="list built-in tool schemas")
    tools_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    tools_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    skills_parser = subparsers.add_parser("skills", aliases=["skill"], help="discover and inspect local skills")
    skills_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    skills_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    skills_sub = skills_parser.add_subparsers(dest="skills_action", required=False)
    skills_list = skills_sub.add_parser("list", help="list discovered skills")
    skills_list.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    skills_list.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    skills_check = skills_sub.add_parser("check", help="validate skill manifests")
    skills_check.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    skills_check.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    skills_show = skills_sub.add_parser("show", help="show one skill manifest and bounded content")
    skills_show.add_argument("skill_id")
    skills_show.add_argument("--include-content", action="store_true")
    skills_show.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    skills_show.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    skills_run = skills_sub.add_parser("run", help="invoke a Markdown or explicitly approved executable skill")
    skills_run.add_argument("skill_id")
    skills_run.add_argument("--input", dest="skill_input", default="{}", help="bounded JSON object passed to the skill")
    skills_run.add_argument("--approve", action="store_true", help="explicitly approve a side-effecting skill")
    skills_run.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    skills_run.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    rules_parser = subparsers.add_parser("rules", help="show bounded scoped project rules")
    rules_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    rules_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    rules_sub = rules_parser.add_subparsers(dest="rules_action", required=False)
    rules_show = rules_sub.add_parser("show", help="show rule sources and combined context")
    rules_show.add_argument("targets", nargs="*", help="optional files/directories whose nested rules apply")
    rules_show.add_argument("--compatible", action="store_true", help="also inspect explicitly compatible rule file names")
    rules_show.add_argument("--include-text", action="store_true", help="include bounded rule text")
    rules_show.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    rules_show.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    rules_check = rules_sub.add_parser("check", help="validate rule sources without executing them")
    rules_check.add_argument("targets", nargs="*")
    rules_check.add_argument("--compatible", action="store_true")
    rules_check.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    rules_check.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    rules_explain = rules_sub.add_parser("explain", help="explain rule precedence and effective sources")
    rules_explain.add_argument("targets", nargs="*", help="files/directories to explain")
    rules_explain.add_argument("--compatible", action="store_true")
    rules_explain.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    rules_explain.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    config_parser = subparsers.add_parser("config", help="inspect or validate typed effective configuration")
    config_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    config_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    config_sub = config_parser.add_subparsers(dest="config_action", required=True)
    config_show = config_sub.add_parser("show", help="show redacted effective config")
    config_show.add_argument("--profile")
    config_show.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    config_show.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    config_validate = config_sub.add_parser("validate", help="validate ignored TOML and environment config")
    config_validate.add_argument("--profile")
    config_validate.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    config_validate.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    config_profiles = config_sub.add_parser("profiles", help="list validated model profiles without secrets")
    config_profiles.add_argument("--show", dest="profile_name")
    config_profiles.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    config_profiles.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    config_policy = config_sub.add_parser("policy", help="explain effective tool permissions without executing them")
    config_policy.add_argument("--profile")
    config_policy.add_argument("--mode", choices=["plan", "act"], help="runtime mode to explain (defaults to configured mode)")
    config_policy.add_argument("--tools", help="optional runtime allow-list to explain")
    config_policy.add_argument("--exclude-tools", help="optional runtime deny-list to explain")
    config_policy.add_argument("--no-tools", action="store_true")
    config_policy.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    config_policy.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    provider_parser = subparsers.add_parser("provider", help="inspect provider configuration without making a request")
    provider_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    provider_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    provider_sub = provider_parser.add_subparsers(dest="provider_action", required=False)
    provider_list = provider_sub.add_parser("list", help="list supported provider adapters")
    provider_list.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    provider_list.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    provider_health = provider_sub.add_parser("health", help="show bounded provider capabilities and configuration")
    provider_health.add_argument("--probe", action="store_true", help="opt in to a bounded provider network probe")
    provider_health.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    provider_health.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    login_parser = subparsers.add_parser("login", help="show secure environment-based credential setup")
    login_parser.add_argument("--provider", default="openai-compatible")
    login_parser.add_argument("--api-key-env", default="FORGECODE_API_KEY")
    login_parser.add_argument("--profile", help="inspect credentials for a named model profile")
    login_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    login_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    trust_parser = subparsers.add_parser("trust", help="inspect or change workspace trust")
    trust_parser.add_argument("action", choices=("status", "grant", "revoke"), nargs="?", default="status")
    trust_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    trust_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    telemetry_parser = subparsers.add_parser("telemetry", help="inspect local telemetry policy and audit records")
    telemetry_parser.add_argument("action", choices=("status", "export"), nargs="?", default="status")
    telemetry_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    telemetry_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    rpc_parser = subparsers.add_parser("rpc", help="serve JSONL requests on stdin")
    rpc_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    rpc_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    # ``transaction``/``rollback`` retain the original ledger-oriented
    # interface.  ``review`` is a first-class evidence report command (rather
    # than an alias) so it can select a session, export/import a signed
    # snapshot, and expose a stable machine-readable envelope.
    transaction_parser = subparsers.add_parser("transaction", aliases=["rollback"], help="review or safely undo a recorded transaction")
    transaction_parser.add_argument("transaction_id", nargs="?", default="latest")
    transaction_parser.add_argument("--execute", action="store_true", help="execute undo after approval")
    transaction_parser.add_argument("--auto-approve", "--yes", action="store_true")
    transaction_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    transaction_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    review_parser = subparsers.add_parser("review", help="build an evidence-driven review and security report")
    review_parser.add_argument("transaction_id", nargs="?", default="latest", help="transaction id to inspect (default: latest)")
    review_parser.add_argument("--transaction", "--transaction-id", dest="transaction_id_option", help="transaction id (alternative to the positional argument)")
    review_parser.add_argument("--session", type=Path, default=None, help="session id/path to inspect (default: latest)")
    review_parser.add_argument("--export", "--export-path", dest="export_path", type=Path, help="write a verified review artifact inside the workspace")
    review_parser.add_argument("--import", "--import-path", dest="import_path", type=Path, help="read and verify a review artifact inside the workspace")
    review_parser.add_argument("--verify", dest="verify_path", type=Path, help="verify an exported review artifact (alias for --import)")
    review_parser.add_argument("--no-verify-files", action="store_true", help="verify artifact integrity but skip current-file digest checks")
    review_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    review_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl", help="emit one bounded machine-readable envelope")
    chat_parser = subparsers.add_parser("chat", aliases=["start", "fc"], help="scriptable interactive coding session")
    chat_parser.add_argument("prompt", nargs="*", help="optional initial task")
    chat_parser.add_argument("--mode", choices=[mode.value for mode in AgentMode], default=AgentMode.ACT.value)
    chat_parser.add_argument("--bypass", action="store_true", help="start chat directly in bypass mode (no approval prompts)")
    chat_parser.add_argument("--auto-approve", "--yes", action="store_true")
    chat_parser.add_argument("--demo", action="store_true")
    chat_parser.add_argument("--demo-task", choices=("calculator", "json"), default="calculator")
    chat_parser.add_argument("--session", type=Path)
    _add_tool_policy_arguments(chat_parser)
    chat_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    chat_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    inspect_parser = subparsers.add_parser("inspect", aliases=["map"], help="inspect a bounded read-only repository map")
    inspect_parser.add_argument("--task", default="repository inspection", help="task used to rank relevant files")
    inspect_parser.add_argument("--budget-chars", type=int, default=20_000)
    inspect_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    inspect_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_parser = subparsers.add_parser("context", help="build and search the bounded local context index")
    context_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_sub = context_parser.add_subparsers(dest="context_action", required=False)
    context_index_parser = context_sub.add_parser("index", help="build or incrementally refresh the context index")
    context_index_parser.add_argument("--rebuild", action="store_true")
    context_index_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_index_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_search_parser = context_sub.add_parser("search", help="search indexed repository text and symbols")
    context_search_parser.add_argument("query", nargs="?", default="")
    context_search_parser.add_argument("--glob")
    context_search_parser.add_argument("--regex")
    context_search_parser.add_argument("--symbol")
    context_search_parser.add_argument("--path")
    context_search_parser.add_argument("--language", help="case-insensitive language filter (for example Python)")
    context_search_parser.add_argument("--line-start", type=int, help="first source line to search (1-based)")
    context_search_parser.add_argument("--line-end", type=int, help="last source line to search (inclusive)")
    context_search_parser.add_argument("--line-range", help="source line range, e.g. 10:20 (inclusive)")
    context_search_parser.add_argument("--max-results", type=int, default=50)
    context_search_parser.add_argument("--context-lines", type=int, default=1)
    context_search_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_search_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_complete_parser = context_sub.add_parser("complete", help="suggest safe relative paths (advisory only)")
    context_complete_parser.add_argument("prefix", nargs="?", default="")
    context_complete_parser.add_argument("--max-results", type=int, default=50)
    context_complete_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_complete_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_show_parser = context_sub.add_parser("show", help="show index metadata and bounded entries")
    context_show_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_show_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_clear_parser = context_sub.add_parser("clear", help="delete the local context index")
    context_clear_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_clear_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_explain_parser = context_sub.add_parser("explain", help="explain indexed and excluded context paths")
    context_explain_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_explain_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    context_diagnostics_parser = context_sub.add_parser("diagnostics", help="report stale indexed files and cache diagnostics")
    context_diagnostics_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    context_diagnostics_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    test_parser = subparsers.add_parser("test", aliases=["tests"], help="run bounded named project test profiles")
    # Keep machine-output flags on both the parent and leaf parsers so callers
    # may put them before or after the action (``test --json list`` and
    # ``test list --json``), matching the other nested CLI commands.
    test_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    test_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    test_parser.add_argument("--mode", choices=[mode.value for mode in AgentMode], help="execution mode: plan refuses side effects; act permits approved checks")
    test_parser.add_argument("--auto-approve", "--yes", action="store_true", help="approve the selected test profile automatically")
    test_parser.add_argument("--session", type=Path, help="JSONL session path for test evidence")
    test_parser.add_argument("--timeout", "--timeout-seconds", type=float, dest="timeout_seconds", help="override the profile timeout for this invocation")
    test_parser.add_argument("--profile", "--name", dest="test_profile_name", help="profile name (an alternative to the run/show positional name)")
    test_sub = test_parser.add_subparsers(dest="test_action", required=False)
    test_list_parser = test_sub.add_parser("list", help="list available test profiles")
    test_list_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    test_list_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    test_show_parser = test_sub.add_parser("show", help="show one test profile")
    test_show_parser.add_argument("name", nargs="?", help="profile name; defaults to the configured profile")
    test_show_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    test_show_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    test_show_parser.add_argument("--profile", "--name", default=argparse.SUPPRESS, dest="test_profile_name")
    test_run_parser = test_sub.add_parser("run", help="run one approved test profile and persist evidence")
    test_run_parser.add_argument("name", nargs="?", help="profile name; defaults to the configured profile")
    test_run_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    test_run_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    test_run_parser.add_argument("--mode", choices=[mode.value for mode in AgentMode], default=argparse.SUPPRESS, dest="mode")
    test_run_parser.add_argument("--auto-approve", "--yes", action="store_true", default=argparse.SUPPRESS, dest="auto_approve")
    test_run_parser.add_argument("--session", type=Path, default=argparse.SUPPRESS, dest="session")
    test_run_parser.add_argument("--timeout", "--timeout-seconds", type=float, default=argparse.SUPPRESS, dest="timeout_seconds")
    test_run_parser.add_argument("--profile", "--name", default=argparse.SUPPRESS, dest="test_profile_name")
    eval_parser = subparsers.add_parser("eval", aliases=["benchmark"], help="score a durable session trajectory")
    eval_parser.add_argument("session_id", type=Path, nargs="?", default=Path("latest"), help="session id/path, default latest")
    eval_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    eval_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    sessions_parser = subparsers.add_parser("sessions", help="list bounded local session records")
    sessions_parser.add_argument("--limit", type=int, default=50)
    sessions_parser.add_argument("--state", choices=["running", "paused", "completed", "failed", "cancelled", "recovery_required"], help="only show sessions whose latest durable state matches")
    sessions_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    sessions_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    diff_parser = subparsers.add_parser("diff", help="show the latest bounded agent change preview")
    diff_parser.add_argument("--session", type=Path, default=Path("latest"))
    diff_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    diff_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    status_parser = subparsers.add_parser("status", help="show latest run and transaction status")
    status_parser.add_argument("--session", type=Path, default=Path("latest"))
    status_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    status_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    session_parser = subparsers.add_parser("session", help="inspect or export one session")
    session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    session_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    session_sub = session_parser.add_subparsers(dest="session_action", required=True)
    show_parser = session_sub.add_parser("show", help="show session metadata and event summary")
    show_parser.add_argument("session_id", type=Path)
    show_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    show_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    export_parser = session_sub.add_parser("export", help="export bounded redacted JSONL")
    export_parser.add_argument("session_id", type=Path)
    export_parser.add_argument("--max-chars", type=int, default=200_000)
    export_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    export_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    inspect_session_parser = session_sub.add_parser("inspect", help="rebuild bounded provider-neutral context")
    inspect_session_parser.add_argument("session_id", type=Path)
    inspect_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    inspect_session_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    compact_session_parser = session_sub.add_parser("compact", help="append a deterministic context summary")
    compact_session_parser.add_argument("session_id", type=Path)
    compact_session_parser.add_argument("--max-chars", type=int, default=24_000)
    compact_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    compact_session_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    fork_session_parser = session_sub.add_parser("fork", help="create a new run linked to a parent session")
    fork_session_parser.add_argument("session_id", type=Path)
    fork_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    fork_session_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    tree_session_parser = session_sub.add_parser("tree", help="show bounded parent/child session metadata")
    tree_session_parser.add_argument("--limit", type=int, default=200)
    tree_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    tree_session_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    clone_session_parser = session_sub.add_parser("clone", help="clone a session as an inspect-only child without replaying effects")
    clone_session_parser.add_argument("session_id", type=Path)
    clone_session_parser.add_argument("--sequence", type=int, default=None)
    clone_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    clone_session_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    import_session_parser = session_sub.add_parser("import", help="import a validated JSONL session as evidence")
    import_session_parser.add_argument("artifact_path", type=Path)
    import_session_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    import_session_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
    plan_parser = subparsers.add_parser("plan", help="create a bounded structured plan without side effects")
    plan_parser.add_argument("prompt", nargs="*", help="task to plan; omit with --session to inspect a stored plan")
    plan_parser.add_argument("--session", type=Path, help="show the latest structured plan event from a session")
    plan_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    plan_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl")
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
    run_parser.add_argument("--require-trust", action="store_true", help="refuse act-mode execution until workspace trust is granted")
    run_parser.add_argument("--profile", help="named model/config profile")
    run_parser.add_argument("--resume", type=Path, help="resume safely from a session id or JSONL path")
    run_parser.add_argument("--fork", action="store_true", help="fork a completed/paused session into a new run id")
    run_parser.add_argument("--dry-run", "--inspect", action="store_true", help="inspect a resume without executing side effects")
    run_parser.add_argument("--force-recovery", action="store_true", help="explicitly acknowledge checkpoint conflicts (still requires approval)")
    _add_tool_policy_arguments(run_parser)
    run_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, dest="json")
    run_parser.add_argument("--jsonl", action="store_true", default=argparse.SUPPRESS, dest="jsonl", help="emit one machine-readable event/result object per line")
    return parser


def _parsed_command_name(args: argparse.Namespace) -> str:
    """Build the stable command label before normal command dispatch.

    This is used for errors raised immediately after argument parsing (for
    example when both machine transports are requested), so it must not touch
    the workspace or configuration and must tolerate partially populated
    namespaces from compatibility callers.
    """
    command = str(getattr(args, "command", None) or "doctor")
    action_fields = {
        "config": "config_action",
        "provider": "provider_action",
        "rules": "rules_action",
        "skills": "skills_action",
        "context": "context_action",
        "test": "test_action",
        "session": "session_action",
    }
    field = action_fields.get(command)
    action = getattr(args, field, None) if field else None
    if action:
        command = f"{command} {action}"
    return command


def _raw_command_name(argv: list[str]) -> str:
    """Best-effort command label for argument-parse failures.

    ``argparse`` may reject a request before it creates a Namespace.  Keep the
    machine protocol useful in that case by selecting the first known command
    and its immediate nested action, while deliberately ignoring option
    values (which may contain arbitrary prompt text).
    """
    commands = {"doctor", "tools", "skills", "skill", "rules", "config", "provider", "login", "trust", "rpc", "transaction", "rollback", "review", "chat", "start", "inspect", "map", "context", "test", "tests", "sessions", "diff", "status", "session", "plan", "run", "eval", "benchmark"}
    actions = {"skills": {"list", "check", "show", "run"}, "skill": {"list", "check", "show", "run"}, "rules": {"show", "check", "explain"}, "config": {"show", "validate", "profiles", "policy"}, "provider": {"health", "list"}, "trust": {"status", "grant", "revoke"}, "context": {"show", "index", "search", "complete", "explain", "diagnostics", "clear"}, "test": {"list", "show", "run"}, "tests": {"list", "show", "run"}, "session": {"show", "export", "inspect", "compact", "fork", "tree", "clone", "import"}}
    command = "doctor"
    command_index = -1
    options_with_values = {
        "--workspace", "--profile", "--session", "--task", "--budget-chars", "--max-steps", "--verify", "--demo-task", "--resume", "--timeout", "--timeout-seconds", "--name", "--input", "--line-range", "--line-start", "--line-end", "--language", "--path", "--glob", "--regex", "--max-results", "--context-lines", "--max-chars", "--transaction", "--transaction-id", "--export", "--export-path", "--import", "--import-path", "--limit", "--tools", "--exclude-tools",
    }
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token in options_with_values:
            skip_next = True
            continue
        if token.startswith("--"):
            continue
        if token in commands:
            command = token
            command_index = index
            break
    canonical = {"skill": "skills", "tests": "test", "start": "chat", "rollback": "transaction", "map": "inspect", "benchmark": "eval"}.get(command, command)
    if command in actions:
        for token in argv[command_index + 1:]:
            if token in actions[command]:
                return f"{canonical} {token}"
    return canonical


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(raw_argv)
    except SystemExit as exc:
        # ``argparse`` writes its usage diagnostic to stderr, which is safe,
        # but a machine caller should still receive one parseable stdout
        # envelope instead of an uncaught SystemExit/empty stream.  Help and
        # version exits (code 0) retain their normal human-facing behavior.
        code = exc.code if isinstance(exc.code, int) else 2
        raw_machine = "--json" in raw_argv or "--jsonl" in raw_argv or any(item.startswith("--json=") or item.startswith("--jsonl=") for item in raw_argv)
        if code != 0 and raw_machine:
            _emit_machine(_machine_error(_raw_command_name(raw_argv), "invalid_arguments", "invalid command-line arguments", exit_code=2))
            return 2
        raise
    # Keep provenance for a narrow v0.0.7 compatibility path.  Argparse
    # merges global and leaf flags, so the parsed namespace alone cannot tell
    # whether an old caller intentionally omitted ``--mode``.  JSONL is
    # always strict; explicit ``--mode`` opts into the v0.0.8 envelope even
    # when the legacy ``--json`` spelling is used.
    raw_has_json = "--json" in raw_argv or any(item.startswith("--json=") for item in raw_argv)
    raw_has_jsonl = "--jsonl" in raw_argv or any(item.startswith("--jsonl=") for item in raw_argv)
    raw_has_mode = "--mode" in raw_argv or any(item.startswith("--mode=") for item in raw_argv)
    setattr(args, "_legacy_json_run", bool(args.command == "run" and raw_has_json and not raw_has_jsonl and not raw_has_mode))
    setattr(args, "_legacy_json_chat_existing", bool(args.command in {"chat", "start"} and raw_has_json and not raw_has_jsonl and "--session" in raw_argv))
    command = args.command or "doctor"
    if command in {"chat", "start"} and getattr(args, "bypass", False):
        args.mode = AgentMode.BYPASS.value
    # ``--json`` and ``--jsonl`` are two spellings of the same canonical
    # machine protocol.  Accepting both silently makes option precedence
    # dependent on parser nesting and can produce duplicate/conflicting
    # streams, so reject the ambiguity before any workspace/config access.
    if bool(getattr(args, "json", False)) and bool(getattr(args, "jsonl", False)):
        _emit_machine(
            _machine_error(
                _parsed_command_name(args),
                "conflicting_output_modes",
                "--json and --jsonl cannot be used together",
                exit_code=2,
            )
        )
        return 2
    machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        message = f"workspace is not a directory: {workspace}"
        if machine_json:
            _emit_machine(_machine_error(command, "invalid_workspace", _redact_display(message), exit_code=2))
        else:
            print(message, file=sys.stderr)
        return 2
    _load_saved_credential(workspace)

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
        message = _redact_display(str(exc))
        if machine_json:
            _emit_machine(_machine_error(command, "config_invalid", message, exit_code=2))
        else:
            print(f"configuration invalid: {message}", file=sys.stderr)
        return 2
    guard = WorkspaceGuard(workspace)
    # Telemetry is deliberately metadata-only and policy-gated.  No prompt,
    # credential, path, or tool output is ever passed to this recorder.
    if settings.effective is not None:
        try:
            Telemetry(workspace, mode=settings.effective.telemetry, offline=settings.effective.offline).record("cli_command", command=command, profile=settings.profile)
        except (ValueError, OSError):
            # Diagnostics must never make an otherwise safe CLI invocation
            # fail; the policy itself remains visible in doctor/config output.
            pass
    base_registry = build_default_registry(guard)
    try:
        cli_tool_policy = parse_tool_policy_options(
            getattr(args, "tools", None),
            getattr(args, "exclude_tools", None),
            no_tools=bool(getattr(args, "no_tools", False)),
            available=base_registry.names(),
        )
    except ConfigError as exc:
        message = _redact_display(str(exc))
        if machine_json:
            _emit_machine(_machine_error(command, "tool_policy_invalid", message, exit_code=2))
        else:
            print(f"invalid tool policy: {message}", file=sys.stderr)
        return 2
    registry = base_registry
    if settings.effective is not None:
        registry = registry.filter(settings.effective.tool_policy)
    if cli_tool_policy is not None:
        registry = registry.filter(cli_tool_policy)
    tool_policy_summary = {
        "requested": {
            "tools": getattr(args, "tools", None),
            "exclude_tools": getattr(args, "exclude_tools", None),
            "no_tools": bool(getattr(args, "no_tools", False)),
        },
        "enabled": list(registry.names()),
        "unavailable": list(registry.unavailable_names()),
    }
    if command == "trust":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = "trust " + getattr(args, "action", "status")
        try:
            store = TrustStore(workspace)
            action = getattr(args, "action", "status")
            payload = store.status() if action == "status" else (store.grant() if action == "grant" else store.revoke())
        except TrustError as exc:
            if machine_json:
                _emit_machine(_machine_error(command_name, "trust_error", str(exc), exit_code=2))
            else:
                print(str(exc), file=sys.stderr)
            return 2
        if machine_json:
            _emit_machine(_machine_envelope(command_name, "trust", True, data=payload, exit_code=0))
        else:
            print("trusted" if payload.get("trusted") else "not trusted")
        return 0
    if command == "login":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        profile_name = getattr(args, "profile", None)
        selected_provider, selected_env = args.provider, args.api_key_env
        selected_model = None
        if profile_name:
            try:
                profile_config = ConfigLoader(workspace).load(profile=profile_name)
                selected_provider = profile_config.provider
                selected_env = profile_config.api_key_env
                selected_model = profile_config.model
            except (ConfigError, OSError) as exc:
                message = str(exc)
                if machine_json: _emit_machine(_machine_error("login", "invalid_profile", message, exit_code=2))
                else: print(message, file=sys.stderr)
                return 2
        if selected_provider not in SUPPORTED_PROVIDERS:
            message = f"unsupported provider: {selected_provider}"
            if machine_json:
                _emit_machine(_machine_error("login", "unsupported_provider", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
        requires_key = provider_requires_credential(selected_provider)
        payload = {"provider": selected_provider, "profile": profile_name, "model": selected_model, "api_key_env": selected_env if requires_key else None, "configured": (not requires_key) or bool(os.getenv(selected_env, "")), "credential": "required" if requires_key else "optional", "storage": "environment-only", "message": (f"Set {selected_env} in the environment; ForgeCode never stores credential values." if requires_key else "Ollama uses a local endpoint and does not require an API key.")}
        if machine_json:
            _emit_machine(_machine_envelope("login", "login", True, data=payload, exit_code=0))
        else:
            print(payload["message"])
            print(f"configured: {payload['configured']}")
        return 0
    if command == "telemetry":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        effective = settings.effective
        mode = effective.telemetry if effective else "off"
        path = workspace / ".forgecode" / "telemetry.jsonl"
        if getattr(args, "action", "status") == "export":
            try:
                content = path.read_text(encoding="utf-8") if path.exists() else ""
            except OSError as exc:
                if machine_json: _emit_machine(_machine_error("telemetry export", "telemetry_error", str(exc), exit_code=2))
                else: print(str(exc), file=sys.stderr)
                return 2
            records = content.splitlines()[-1000:]
            total_count = len(content.splitlines())
            payload = {"mode": mode, "offline": bool(effective.offline) if effective else False, "records": records, "count": total_count, "returned_count": len(records), "truncated": total_count > len(records), "sha256": hashlib_lib.sha256("\n".join(records).encode("utf-8")).hexdigest()}
        else:
            payload = {"mode": mode, "offline": bool(effective.offline) if effective else False, "local_path": ".forgecode/telemetry.jsonl", "exists": path.exists()}
        if machine_json: _emit_machine(_machine_envelope("telemetry " + args.action, "telemetry", True, data=payload, exit_code=0))
        else: print(jsonlib.dumps(payload, ensure_ascii=False) if args.action == "export" else f"telemetry={mode} offline={payload['offline']} local_exists={payload['exists']}")
        return 0
    if command == "rpc":
        from ..rpc import serve_lines
        for response in serve_lines(sys.stdin):
            print(response)
        return 0
    # Act-mode executions require an explicit, persisted workspace trust
    # decision. Deterministic offline demos remain available in fresh folders
    # without trust so assessment smoke tests stay self-contained.
    effective_mode = getattr(args, "mode", None) or (settings.effective.default_mode if settings.effective else None)
    provider_configured = bool(settings.model and (not provider_requires_credential(settings.effective.provider if settings.effective else "openai-compatible") or (settings.api_key_env and os.getenv(settings.api_key_env, ""))))
    if command in {"run", "chat", "start"} and effective_mode in {"act", "bypass"} and (getattr(args, "require_trust", False) or provider_configured) and not getattr(args, "demo", False):
        try:
            trust = TrustStore(workspace).status()
        except TrustError as exc:
            message = str(exc)
            if machine_json:
                _emit_machine(_machine_error(command, "trust_error", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
        if not trust.get("trusted"):
            message = "workspace is not trusted; run `forgecode trust grant` before act-mode execution"
            if machine_json:
                _emit_machine(_machine_error(command, "trust_required", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
    if command == "run":
        args.mode = args.mode or (settings.effective.default_mode if settings.effective else AgentMode.ACT.value)
        args.max_steps = args.max_steps if args.max_steps is not None else (settings.effective.max_steps if settings.effective else None)
    if command in {"test", "tests"}:
        # Test profiles are side-effecting by nature (they execute a process),
        # so use the typed project default while still allowing an explicit
        # plan/act choice.  ``getattr`` keeps parser aliases and leaf actions
        # compatible with older callers that construct a Namespace directly.
        args.mode = getattr(args, "mode", None) or (settings.effective.default_mode if settings.effective else AgentMode.ACT.value)

    if command == "config":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = f"config {getattr(args, 'config_action', 'show')}"
        try:
            if args.config_action == "policy":
                config = ConfigLoader(workspace).load(profile=getattr(args, "profile", None))
                rules = RuleEngine(guard, compatible=True).discover(())
                effective_mode = getattr(args, "mode", None) or config.default_mode
                available = set(base_registry.names())
                effective_policy = config.tool_policy
                runtime_policy = parse_tool_policy_options(getattr(args, "tools", None), getattr(args, "exclude_tools", None), no_tools=bool(getattr(args, "no_tools", False)), available=tuple(sorted(available)))
                rows = []
                trusted = TrustStore(workspace).status().get("trusted", False)
                for name in sorted(available):
                    enabled = effective_policy.permits(name, available=available)
                    reasons = []
                    if name in effective_policy.deny: reasons.append("config_deny")
                    elif effective_policy.allow and name not in effective_policy.allow: reasons.append("config_not_allowlisted")
                    if runtime_policy is not None:
                        enabled = enabled and runtime_policy.permits(name, available=available)
                        if name in runtime_policy.deny: reasons.append("runtime_deny")
                        elif runtime_policy.allow and name not in runtime_policy.allow: reasons.append("runtime_not_allowlisted")
                    if effective_mode == "plan" and name in {"write_file", "apply_patch", "run_command"}: reasons.append("plan_mode_read_only"); enabled = False
                    if not trusted and effective_mode == "act" and name in {"write_file", "apply_patch", "run_command"}: reasons.append("workspace_trust_required"); enabled = False
                    rows.append({"tool": name, "enabled": enabled, "reasons": reasons or ["permitted_by_current_policy"], "approval": config.approval, "mode": effective_mode, "trust": trusted})
                payload = {"profile": config.profile, "provider": config.provider, "mode": effective_mode, "configured_mode": config.default_mode, "approval": config.approval, "trust": trusted, "rules": {"fingerprint": rules.fingerprint, "sources": [{"path": item.path, "scope": item.scope, "priority": item.priority, "digest": item.digest} for item in rules.sources], "diagnostics": [item.to_dict() for item in rules.diagnostics]}, "config_policy": {"allow": list(effective_policy.allow), "deny": list(effective_policy.deny)}, "runtime_policy": None if runtime_policy is None else {"allow": list(runtime_policy.allow), "deny": list(runtime_policy.deny)}, "tools": rows}
            elif args.config_action == "profiles":
                profiles = ConfigLoader(workspace).profiles()
                selected = getattr(args, "profile_name", None)
                if selected:
                    profile = next((item for item in profiles if item.name == selected), None)
                    if profile is None:
                        raise ConfigError(f"profile not found: {selected}")
                    payload = {"valid": True, "profiles": [profile.to_dict()]}
                else:
                    payload = {"valid": True, "profiles": [item.to_dict() for item in profiles]}
            else:
                config = ConfigLoader(workspace).load(profile=getattr(args, "profile", None))
                payload = {"valid": True, "config": config.to_dict(), "sources": list(config.sources)}
        except ConfigError as exc:
            safe_message = _redact_display(str(exc))
            if machine_json:
                # ``valid`` remains an additive compatibility field; the
                # canonical failure details live under ``error``.
                envelope = _machine_error(command_name, "config_invalid", safe_message, exit_code=2)
                envelope["valid"] = False
                _emit_machine(envelope)
            elif args.config_action == "validate":
                print("invalid: " + safe_message, file=sys.stderr)
            else:
                print("invalid: " + safe_message)
            return 2
        if machine_json:
            envelope = _machine_envelope(command_name, "config", True, data=payload, exit_code=0, **_compat_aliases(payload))
            if getattr(args, "jsonl", False):
                envelope["type"] = "config"
            _emit_machine(envelope)
        else:
            if args.config_action == "profiles":
                for item in payload["profiles"]:
                    print(f"{item['name']} provider={item['provider']} model={item['model'] or '<unset>'} streaming={item['streaming']} key={item['api_key_env']} configured={item['api_key_configured']}")
            elif args.config_action == "policy":
                print(f"policy profile={payload['profile']} mode={payload['mode']} approval={payload['approval']} trust={payload['trust']}")
                for row in payload["tools"]:
                    print(f"{row['tool']}: {'enabled' if row['enabled'] else 'disabled'} ({', '.join(row['reasons'])})")
            else:
                print("configuration: valid")
                for key, value in config.to_dict().items():
                    print(f"{key}: {value}")
        return 0

    if command == "provider":
        action = getattr(args, "provider_action", None) or "health"
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = f"provider {action}"
        if action == "list":
            rows = list(provider_metadata())
            if machine_json:
                _emit_machine(_machine_envelope(command_name, "provider_list", True, data={"providers": rows}, exit_code=0))
            else:
                for row in rows: print(f"{row['name']} streaming={row['streaming']} local={row['local']} credential={row['credential']}")
            return 0
        if action != "health":
            if machine_json:
                _emit_machine(_machine_error(command_name, "unknown_action", "unknown provider action", exit_code=2))
            else:
                print("unknown provider action", file=sys.stderr)
            return 2
        effective = settings.effective
        payload = {
            "provider": effective.provider if effective else "openai-compatible",
            "profile": settings.profile,
            "model": settings.model,
            "base_url": redact_text(settings.base_url),
            "configured": bool(settings.model and (not provider_requires_credential(effective.provider) or os.getenv(settings.api_key_env, ""))),
            "streaming": effective.streaming if effective else "auto",
            "capabilities": {
                "tool_calling": True,
                "json_mode": False,
                "streaming": bool(effective and effective.streaming in {"on", "required"}),
                "max_input_chars": 4_000_000,
                "max_output_chars": 4_000_000,
            },
            "network_request": False,
        }
        if getattr(args, "probe", False):
            if effective and effective.offline:
                payload["probe"] = {"requested": True, "performed": False, "reason": "offline policy blocks network"}
            elif not payload["configured"]:
                payload["probe"] = {"requested": True, "performed": False, "reason": "provider is not configured"}
            else:
                payload["probe"] = _provider_probe(settings.base_url, timeout=effective.provider_timeout_seconds if effective else 3.0)
        else:
            payload["probe"] = {"requested": False, "performed": False, "reason": "offline by default"}
        payload["credential"] = "required" if provider_requires_credential(payload["provider"]) else "optional"
        if machine_json:
            envelope = _machine_envelope(command_name, "provider_health", True, data=payload, exit_code=0, **_compat_aliases(payload))
            if getattr(args, "jsonl", False):
                envelope["type"] = "provider_health"
            _emit_machine(envelope)
        else:
            print(f"provider={payload['provider']} model={payload['model'] or '<unset>'} configured={payload['configured']}")
            print(f"streaming={payload['streaming']} network_request=false")
        return 0

    if command in {"eval", "benchmark"}:
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = "eval"
        try:
            session_path = _resolve_session_reference(guard, workspace, args.session_id, must_exist=True)
            score = evaluate_session(SessionStore(session_path))
            payload = {"session": guard.relative(session_path), "score": score.to_dict()}
            code = 0 if score.status == "completed" else (130 if score.cancelled else (3 if score.status == "recovery_required" else 1))
            if machine_json:
                if code == 0:
                    _emit_command_payload(args, command_name, "trajectory_score", payload, aliases=True, type_label="trajectory_score")
                else:
                    envelope = _machine_error(command_name, "trajectory_incomplete", "trajectory did not meet completion criteria", exit_code=code, details=payload)
                    envelope.update(_compat_aliases(payload)); envelope["type"] = "trajectory_score"; _emit_machine(envelope)
            else:
                print(f"trajectory session={payload['session']} status={score.status} score={score.score:.3f} tools={score.tool_calls} failures={score.failures} compactions={score.compactions}")
            return code
        except (OSError, ValueError, SessionFormatError) as exc:
            message = _redact_display(str(exc))
            if machine_json:
                _emit_machine(_machine_error(command_name, "invalid_session", message, exit_code=2))
            else:
                print(f"evaluation failed: {message}", file=sys.stderr)
            return 2

    if command == "rules":
        action = args.rules_action or "show"
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = f"rules {action}"
        try:
            rules = RuleEngine(guard, compatible=getattr(args, "compatible", False)).discover(getattr(args, "targets", ()))
        except (OSError, ValueError) as exc:
            if machine_json:
                _emit_machine(_machine_error(command_name, "rules_failed", _redact_display(str(exc)), exit_code=2))
            else:
                print(f"rules failed: {_redact_display(str(exc))}", file=sys.stderr)
            return 2
        payload = rules.to_dict(include_text=bool(getattr(args, "include_text", False) and action == "show"))
        if action == "explain":
            payload["precedence"] = "higher priority values are deeper target scopes; source order is deterministic"
            payload["effective_sources"] = [{"path": source.path, "scope": source.scope, "priority": source.priority, "kind": source.kind, "digest": source.digest} for source in rules.sources]
        if action == "check":
            payload["valid"] = not any(item.severity == "error" for item in rules.diagnostics)
        if machine_json:
            ok = not any(item.severity == "error" for item in rules.diagnostics)
            if ok:
                aliases = _compat_aliases(payload)
                envelope = _machine_envelope(command_name, "rules", True, data=payload, exit_code=0, **aliases)
            else:
                envelope = _machine_envelope(
                    command_name,
                    "error",
                    False,
                    error={"code": "rule_diagnostics", "message": "project rules contain errors", "details": {"diagnostics": [item.to_dict() for item in rules.diagnostics][:100]}},
                    data=None,
                    exit_code=1,
                )
                # Keep the old rule payload available to compatibility clients
                # even when diagnostics make the command unsuccessful.
                envelope.update(_compat_aliases(payload))
            if getattr(args, "jsonl", False):
                envelope["type"] = "rules"
            _emit_machine(envelope)
        else:
            print(f"rules sources={len(rules.sources)} diagnostics={len(rules.diagnostics)} fingerprint={rules.fingerprint[:16]}")
            for source in rules.sources:
                print(f"{source.path} scope={source.scope} priority={source.priority} chars={source.chars} digest={source.digest[:16]}")
            for diagnostic in rules.diagnostics:
                print(f"{diagnostic.severity}: {diagnostic.code}: {diagnostic.message}")
            if getattr(args, "include_text", False) and rules.text:
                print(rules.render())
        return 0 if not any(item.severity == "error" for item in rules.diagnostics) else 1

    if command in {"skills", "skill"}:
        loader = SkillLoader(guard)
        skills = loader.discover()
        registry_skills = SkillRegistry(skills)
        action = getattr(args, "skills_action", None) or "list"
        command_name = f"skills {action}"
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        try:
            if action == "check":
                payload = {"valid": not loader.errors, "skills": [item.to_dict() for item in skills], "errors": list(loader.errors), "diagnostics": list(loader.diagnostics)}
                code = 0 if not loader.errors else 1
            elif action == "show":
                skill = registry_skills.get(args.skill_id)
                payload = skill.to_dict(include_content=bool(args.include_content))
                code = 0
            elif action == "run":
                if not isinstance(args.skill_input, str) or len(args.skill_input) > MAX_SKILL_INPUT_CHARS:
                    raise SkillError(f"--input exceeds the {MAX_SKILL_INPUT_CHARS}-character safety limit")
                try:
                    skill_arguments = bounded_json_loads(args.skill_input, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON value: {value}")))
                except (TypeError, jsonlib.JSONDecodeError, ValueError) as exc:
                    raise SkillError("--input must be a valid finite JSON object") from exc
                if not isinstance(skill_arguments, dict):
                    raise SkillError("--input must be a JSON object")
                invocation = registry_skills.invoke(args.skill_id, skill_arguments, executor=SkillExecutor(guard), approved=bool(args.approve))
                payload = invocation.to_dict()
                code = 0 if invocation.ok else 1
            else:
                payload = {"skills": [item.to_dict() for item in skills], "errors": list(loader.errors), "diagnostics": list(loader.diagnostics)}
                code = 0 if not loader.errors else 1
        except (SkillError, OSError, ValueError) as exc:
            payload = {"ok": False, "error": _redact_display(str(exc))}
            code = 2
        if machine_json:
            # JSONL is intentionally one complete command envelope rather
            # than a heterogeneous stream of skill/error/diagnostic records.
            # This keeps consumers line-oriented without requiring a second
            # protocol.  ``type`` is a harmless compatibility alias for
            # clients that used the pre-v0.0.8 stream labels.
            if payload.get("ok") is False:
                raw_error = payload.get("error")
                if isinstance(raw_error, dict):
                    error = raw_error
                else:
                    error = {"code": "skill_failed", "message": _redact_display(str(raw_error or "skill invocation failed"))[:2_000]}
                envelope = _machine_envelope(command_name, "error", False, error=error, exit_code=code)
            elif action in {"list", "check"} and payload.get("errors"):
                # A manifest validation error is a command failure, but the
                # bounded diagnostics are retained under ``error.details`` so
                # automation can still explain which entries were affected.
                envelope = _machine_envelope(
                    command_name,
                    "error",
                    False,
                    error={
                        "code": "skill_manifest_error",
                        "message": "one or more skill manifests failed validation",
                        "details": {"errors": list(payload.get("errors", ()))[:100], "diagnostics": list(payload.get("diagnostics", ()))[:100]},
                    },
                    exit_code=code,
                )
            else:
                kind = {"list": "skill_list", "check": "skill_check", "show": "skill", "run": "skill_result"}.get(action, "skills")
                # Keep the v0.0.7 top-level result keys as additive aliases;
                # canonical consumers should read ``data``.
                aliases = {key: value for key, value in payload.items() if key in {"skills", "errors", "diagnostics", "manifest", "content", "output", "invocation"}}
                envelope = _machine_envelope(command_name, kind, True, data=payload, exit_code=code, **_compat_aliases(aliases))
            if getattr(args, "jsonl", False):
                envelope["type"] = "skill_summary"
            _emit_machine(envelope)
        else:
            if payload.get("ok") is False:
                print("skills failed: " + payload["error"], file=sys.stderr)
            elif action == "show":
                manifest = payload["manifest"]
                print(f"{manifest['id']} {manifest['version']}: {manifest['description']}")
                if "content" in payload:
                    print(payload["content"])
            elif action == "run":
                print(payload.get("output", payload.get("error", "")))
            else:
                for item in payload.get("skills", []):
                    manifest = item["manifest"]
                    print(f"{manifest['id']} {manifest['version']} side_effect={manifest['side_effect']} path={item['path']}")
                for error_message in payload.get("errors", []):
                    print("error: " + error_message, file=sys.stderr)
        return code

    if command == "context":
        action = getattr(args, "context_action", None) or "show"
        command_name = f"context {action}"
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        index = ContextIndex(guard)
        try:
            if action == "index":
                payload = index.ensure() if not getattr(args, "rebuild", False) else index.build(rebuild=True)
                payload = payload.to_dict()
            elif action == "search":
                index.ensure()
                line_start = getattr(args, "line_start", None)
                line_end = getattr(args, "line_end", None)
                shorthand = getattr(args, "line_range", None)
                if shorthand is not None:
                    if line_start is not None or line_end is not None:
                        raise ValueError("--line-range cannot be combined with --line-start/--line-end")
                    match = re.fullmatch(r"\s*(\d+)\s*(?::|-|\.\.)\s*(\d+)\s*", shorthand)
                    if not match:
                        raise ValueError("--line-range must use START:END")
                    line_range = (int(match.group(1)), int(match.group(2)))
                else:
                    line_range = None if line_start is None and line_end is None else (1 if line_start is None else line_start, 10_000_000 if line_end is None else line_end)
                matches = index.search(args.query, glob=args.glob, regex=args.regex, symbol=args.symbol, path=args.path, language=getattr(args, "language", None), line_range=line_range, max_results=args.max_results, context_lines=args.context_lines)
                payload = {"query": args.query, "count": len(matches), "results": [item.to_dict() for item in matches], "stale": list(index.last_search_issues), "index": index.show(), "filters": {"language": getattr(args, "language", None), "line_range": list(line_range) if line_range else None}}
            elif action == "complete":
                index.ensure()
                suggestions = index.complete(args.prefix, max_results=args.max_results)
                payload = {"prefix": args.prefix, "count": len(suggestions), "results": list(suggestions), "advisory": True}
            elif action == "explain":
                index.ensure()
                payload = index.explain()
            elif action == "diagnostics":
                # Diagnostics intentionally inspect the existing cache before
                # refreshing it; otherwise a changed file would be silently
                # reindexed and the caller could never observe staleness.
                payload = index.diagnostics()
            elif action == "clear":
                payload = {"cleared": index.clear(), "path": index.guard.relative(index.path)}
            else:
                index.ensure()
                payload = index.show()
            code = 0
        except (ContextIndexError, OSError, UnicodeError, ValueError) as exc:
            payload = {"ok": False, "error": _redact_display(str(exc))}
            code = 2
        if machine_json:
            if payload.get("ok") is False:
                raw_error = payload.get("error")
                error = raw_error if isinstance(raw_error, dict) else {"code": "context_failed", "message": _redact_display(str(raw_error or "context command failed"))[:2_000]}
                envelope = _machine_envelope(command_name, "error", False, error=error, exit_code=code)
            else:
                kind = {
                    "index": "context_index",
                    "search": "context_search",
                    "show": "context_index",
                    "explain": "context_explain",
                    "diagnostics": "context_diagnostics",
                    "clear": "context_clear",
                    "complete": "context_complete",
                }.get(action, "context")
                # Additive aliases preserve the pre-v0.0.8 context JSON shape
                # while exposing the stable envelope for new integrations.
                aliases = {key: value for key, value in payload.items() if key in {"query", "count", "results", "stale", "filters", "index", "path", "files", "added", "updated", "removed", "omitted", "counts", "fingerprint", "included", "excluded", "cleared", "errors"}}
                envelope = _machine_envelope(command_name, kind, True, data=payload, exit_code=code, **_compat_aliases(aliases))
            if getattr(args, "jsonl", False):
                # Preserve the old final-record label as an additive field;
                # unlike the pre-v0.0.8 stream, this is still one complete
                # envelope and includes all bounded search results in data.
                envelope["type"] = "context_summary" if action == "search" else "context_result"
            _emit_machine(envelope)
        else:
            if payload.get("ok") is False:
                print("context failed: " + payload["error"], file=sys.stderr)
            elif action == "search":
                for item in payload.get("results", []):
                    print(f"{item['path']}:{item['line']} [{item['reason']}]\n{item['snippet']}")
                print(f"matches={payload['count']}")
            elif action == "complete":
                for item in payload.get("results", []):
                    suffix = f" (excluded: {item.get('exclusion_reason')})" if item.get("excluded") else ""
                    print(f"{item['path']}{suffix}")
                print(f"suggestions={payload['count']} advisory=true")
            elif action == "index":
                print(f"index={payload['path']} files={payload['files']} added={payload['added']} updated={payload['updated']} removed={payload['removed']} omitted={payload['omitted']}")
                for error_message in payload.get("errors", []):
                    print("error: " + error_message, file=sys.stderr)
            elif action == "explain":
                print(f"included={payload.get('included', 0)} excluded={len(payload.get('excluded', []))} errors={len(payload.get('errors', []))}")
                for item in payload.get("excluded", [])[:100]:
                    print(f"excluded {item.get('path', '<unknown>')}: {item.get('reason', 'unknown')}")
                for error_message in payload.get("errors", [])[:100]:
                    print("error: " + str(error_message), file=sys.stderr)
            elif action == "diagnostics":
                stale = payload.get("stale", [])
                print(f"stale={len(stale)} errors={len(payload.get('errors', []))}")
                for item in stale[:100]:
                    print(f"stale {item.get('path', '<unknown>')}: {item.get('reason', 'unknown')}")
                for error_message in payload.get("errors", [])[:100]:
                    print("error: " + str(error_message), file=sys.stderr)
            else:
                print(f"index={payload['path']} files={payload['counts']['files']} fingerprint={payload['fingerprint'][:16]}")
        return code

    if command in {"test", "tests"}:
        # Test profiles deliberately have their own command namespace instead
        # of overloading ``run --verify``.  This keeps profile selection,
        # approval and evidence explicit while preserving the existing agent
        # run and interactive ``/test`` paths.
        action = getattr(args, "test_action", None) or "list"
        if action not in {"list", "show", "run"}:
            action = "list"
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = f"test {action}"

        def emit_test(payload: dict[str, Any], *, human: Any = None) -> None:
            """Render one bounded test envelope without contaminating JSON stdout."""
            if machine_json:
                # Both ``--json`` and ``--jsonl`` use the same canonical
                # envelope.  A failed profile run may still have useful
                # evidence; place it in bounded error details and retain the
                # old ``evidence``/``session`` aliases at the top level rather
                # than violating the data/error XOR invariant.
                ok = bool(payload.get("ok", False))
                if ok:
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                    aliases = _compat_aliases({key: value for key, value in payload.items() if key not in {"schema_version", "kind", "ok", "command", "data", "error", "exit_code"}})
                    envelope = _machine_envelope(command_name, str(payload.get("kind") or "test_result"), True, data=data, exit_code=0, **aliases)
                else:
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else None
                    status = str((evidence or {}).get("verification_status", "failed"))
                    error_code = {
                        "timed_out": "test_timeout",
                        "cancelled": "cancelled",
                        "denied": "approval_denied",
                        "skipped": "test_not_executed",
                    }.get(status, "test_failed")
                    if isinstance(evidence, dict) and evidence.get("error_code"):
                        raw_code = str(evidence["error_code"])
                        if raw_code in {"approval_denied", "cancelled", "timeout", "teardown_timeout"}:
                            error_code = {"timeout": "test_timeout", "teardown_timeout": "test_timeout"}.get(raw_code, raw_code)
                    exit_code = 130 if error_code == "cancelled" else 1
                    details = {"evidence": evidence} if evidence is not None else {"result": data}
                    aliases = _compat_aliases({key: value for key, value in data.items() if key not in {"data", "error"}})
                    envelope = _machine_error(command_name, error_code, "test profile did not pass", exit_code=exit_code, details=details)
                    envelope.update(aliases)
                envelope["type"] = str(payload.get("kind") or "test_result")
                _emit_machine(envelope)
                return
            if human is not None:
                print(human)

        def test_error(code: str, message: str, *, exit_code: int = 2) -> int:
            secret_names = {"FORGECODE_API_KEY"}
            effective_config = getattr(settings, "effective", None)
            if effective_config is not None:
                secret_names.add(str(effective_config.api_key_env))
            secret_values = [os.getenv(name, "") for name in secret_names]
            safe_message = _redact_display(str(message), secret_values)
            if machine_json:
                envelope = _machine_error(command_name, code, safe_message, exit_code=exit_code)
                envelope["type"] = "error"
                _emit_machine(envelope)
            else:
                print(f"forgecode {command_name} failed: {safe_message}", file=sys.stderr)
            return exit_code

        try:
            profiles = TestProfileLoader(workspace).load()
        except (TestProfileError, OSError, ValueError) as exc:
            return test_error("invalid_profile_config", str(exc))

        # A positional leaf argument wins over the optional ``--profile`` /
        # ``--name`` spelling.  When neither is supplied the loader applies
        # FORGECODE_TEST_PROFILE and then the configured default profile.
        explicit_name = getattr(args, "name", None) or getattr(args, "test_profile_name", None)
        env_name = os.getenv("FORGECODE_TEST_PROFILE")
        selected_by = "cli" if explicit_name else ("environment" if env_name else "default")
        try:
            profile = profiles.get(explicit_name, env=os.environ)
        except TestProfileError as exc:
            return test_error("profile_not_found", str(exc))

        if action == "list":
            data = {
                "profiles": [item.to_dict() for item in profiles.profiles],
                "default_profile": profiles.default_profile,
                "source": profiles.source,
                "selected_profile": profile.name,
                "selected_by": selected_by,
            }
            payload = {"schema_version": 1, "kind": "test_profiles", "ok": True, "command": command_name, "data": data}
            if machine_json:
                emit_test(payload)
            else:
                source = profiles.source or "built-in defaults"
                print(f"test profiles (default={profiles.default_profile}, selected={profile.name}, source={source})")
                for item in profiles.profiles:
                    marker = " *" if item.name == profiles.default_profile else ""
                    command = repr(list(item.command))
                    print(f"{item.name}{marker}: {command} cwd={item.cwd} timeout={item.timeout_seconds:g}s approval={item.approval}")
                    if item.description:
                        print(f"  {item.description}")
            return 0

        if action == "show":
            data = {
                "profile": profile.to_dict(),
                "default_profile": profiles.default_profile,
                "source": profiles.source,
                "selected_by": selected_by,
            }
            payload = {"schema_version": 1, "kind": "test_profile", "ok": True, "command": command_name, "data": data}
            if machine_json:
                emit_test(payload)
            else:
                print(f"profile={profile.name} cwd={profile.cwd} timeout={profile.timeout_seconds:g}s approval={profile.approval}")
                print(f"command={list(profile.command)!r}")
                if profile.setup is not None:
                    print(f"setup={list(profile.setup)!r}")
                if profile.teardown is not None:
                    print(f"teardown={list(profile.teardown)!r}")
                print(f"env_allow={list(profile.env_allow)!r}")
                print(f"output={profile.output.to_dict()} expected_exit={profile.expected_exit.to_dict()}")
                if profile.description:
                    print(profile.description)
            return 0

        # ``test run`` is the only profile action that creates a session event.
        # A fresh session per invocation prevents a new run identity from being
        # appended to an older stream and makes cross-process evidence easy to
        # attribute.  Existing streams should be continued through the
        # session/resume workflows instead.
        mode = getattr(args, "mode", None) or (settings.effective.default_mode if settings.effective else AgentMode.ACT.value)
        api_key_env = settings.effective.api_key_env if settings.effective else "FORGECODE_API_KEY"
        api_key = os.getenv(api_key_env, "")
        auto_approve = bool(getattr(args, "auto_approve", False))
        configured_approval = settings.effective.approval if settings.effective else "interactive"
        if mode == AgentMode.PLAN.value:
            approval = DenyAllApproval()
        elif profile.approval == "auto" and configured_approval not in {"deny"} and not auto_approve:
            # ``approval = None`` lets TestProfileRunner honor the profile's
            # explicit auto policy.  Passing a DenyAllApproval here would
            # accidentally turn an intentionally non-interactive profile into
            # a denial in JSON/JSONL mode.
            approval = None
        elif auto_approve or configured_approval == "auto":
            approval = InteractiveApproval(auto_approve=True, output_fn=_approval_output(machine_json), prompt_to_output=machine_json, secrets=[api_key])
        elif configured_approval == "deny" or machine_json:
            # Machine invocations cannot safely block waiting for stdin.  A
            # denial is represented by the runner's structured evidence.
            approval = DenyAllApproval()
        else:
            approval = InteractiveApproval(output_fn=_approval_output(False), secrets=[api_key])
        new_run_id = uuid.uuid4().hex
        try:
            requested_session = getattr(args, "session", None)
            session_path = _resolve_session_reference(guard, workspace, requested_session) if requested_session else _new_session_path(guard, new_run_id)
            if requested_session and session_path.is_file() and session_path.stat().st_size > 0:
                return test_error("session_not_new", "test run refuses to append to an existing session; choose a new session path or inspect it")
            session = SessionStore(session_path, secrets=[api_key], run_id=new_run_id, mode=mode)
        except (OSError, ValueError, SessionFormatError) as exc:
            return test_error("invalid_session_path", str(exc), exit_code=2)
        try:
            runner = TestProfileRunner(guard, approval=approval, session=session, mode=mode, secrets=[api_key])
            evidence = runner.run(profile, timeout_seconds=getattr(args, "timeout_seconds", None))
        except KeyboardInterrupt:
            # The subprocess runner cooperatively records cancellation when a
            # callback is supplied.  Ctrl-C before execution still receives a
            # deterministic non-success CLI result and never a false pass.
            return test_error("cancelled", "test run cancelled", exit_code=130)
        except (TestProfileError, OSError, ValueError) as exc:
            return test_error("test_run_failed", str(exc), exit_code=2)

        data = {
            "evidence": evidence.to_dict(),
            "session": guard.relative(session_path),
            "selected_by": selected_by,
        }
        payload = {"schema_version": 1, "kind": "test_profile_result", "ok": bool(evidence.ok), "command": command_name, "data": data}
        if machine_json:
            emit_test(payload)
        else:
            status = evidence.verification_status
            exit_text = "<none>" if evidence.exit_code is None else str(evidence.exit_code)
            print(f"profile={evidence.profile} status={status} ok={evidence.ok} exit_code={exit_text} duration={evidence.duration_seconds:g}s")
            print(f"session={guard.relative(session_path)}")
            if evidence.stdout_preview:
                print("[stdout]")
                print(evidence.stdout_preview)
            if evidence.stderr_preview:
                print("[stderr]")
                print(evidence.stderr_preview, file=sys.stderr)
            if evidence.error_code:
                print(f"error={evidence.error_code}", file=sys.stderr)
        return 0 if evidence.ok else 1

    if command == "plan":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = "plan"
        prompt = " ".join(args.prompt).strip()
        try:
            if args.session:
                session_path = _resolve_session_reference(guard, workspace, args.session, must_exist=True)
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
            if machine_json:
                envelope = _machine_error(command_name, "plan_invalid", message, exit_code=2)
                envelope["message"] = message[:2_000]  # legacy alias
                envelope["error_code"] = "plan_invalid"
                _emit_machine(envelope)
            else:
                print(f"plan failed: {message}", file=sys.stderr)
            return 2
        if machine_json:
            envelope = _machine_envelope(command_name, "plan", True, data=payload, exit_code=0, **_compat_aliases(payload))
            envelope["type"] = "plan"
            _emit_machine(envelope)
        else:
            print(f"plan={plan.plan_id} revision={plan.revision} mode=plan stale={plan.stale}")
            for item in plan.items:
                print(f"{item.id}: {item.title} [{item.status}] risk={item.risk}")
            print("No files or commands were executed.")
        return 0

    if command == "review":
        # The review command is intentionally separate from the legacy
        # transaction alias.  It consumes only durable evidence through the
        # application facade and emits one stable envelope in machine modes.
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        jsonl_mode = bool(getattr(args, "jsonl", False))
        command_name = "review"

        def review_error(code: str, message: str, *, exit_code: int = 2) -> int:
            safe_message = _redact_display(str(message), [os.getenv(settings.api_key_env, "")])[:2_000]
            if machine_json:
                # JSON and JSONL are both one complete bounded envelope.  No
                # progress or exception text is allowed on stdout.
                _emit_machine(_machine_error(command_name, code, safe_message, exit_code=exit_code))
            else:
                print(f"forgecode review failed: {safe_message}", file=sys.stderr)
            return exit_code

        transaction_id = getattr(args, "transaction_id", "latest") or "latest"
        option_transaction = getattr(args, "transaction_id_option", None)
        if option_transaction:
            if transaction_id not in {"", "latest"} and transaction_id != option_transaction:
                return review_error("conflicting_transaction", "positional transaction id and --transaction disagree")
            transaction_id = option_transaction
        requested_session = getattr(args, "session", None)
        session_reference: Path | None = None
        try:
            if requested_session is not None and str(requested_session) != "latest":
                session_reference = _resolve_session_reference(guard, workspace, requested_session, must_exist=True)
        except (OSError, ValueError, SessionFormatError) as exc:
            return review_error("invalid_session", str(exc))

        import_path = getattr(args, "import_path", None)
        verify_path = getattr(args, "verify_path", None)
        export_path = getattr(args, "export_path", None)
        if import_path is not None and verify_path is not None:
            return review_error("conflicting_artifacts", "--import and --verify cannot be combined")
        artifact_input = verify_path or import_path
        if artifact_input is not None and export_path is not None:
            return review_error("conflicting_artifacts", "--export cannot be combined with --import/--verify")
        if artifact_input is not None and requested_session is not None:
            return review_error("conflicting_artifacts", "--session cannot be combined with artifact verification")

        api_key = os.getenv(settings.api_key_env, "")
        service = ReviewService(guard, secrets=(api_key,) if api_key else ())
        artifact_meta: dict[str, Any] | None = None
        try:
            if artifact_input is not None:
                report = service.import_report(artifact_input, verify_files=not bool(getattr(args, "no_verify_files", False)))
                artifact_meta = {
                    "action": "verify" if verify_path is not None else "import",
                    "path": guard.relative(guard.resolve(artifact_input, must_exist=True)),
                    "verify_files": not bool(getattr(args, "no_verify_files", False)),
                }
            else:
                report = service.build(session=session_reference, transaction_id=transaction_id)
                if export_path is not None:
                    destination = service.export(report, export_path)
                    artifact_meta = {"action": "export", "path": guard.relative(destination), "verify_files": True}
        except ReviewArtifactError as exc:
            lowered = str(exc).lower()
            # Integrity, stale-file and cross-workspace failures are explicit
            # conflicts (3); malformed CLI/artifact input remains 2.
            code = 3 if any(token in lowered for token in ("stale", "digest", "workspace", "changed", "tamper", "conflict")) else 2
            return review_error("artifact_invalid", str(exc), exit_code=code)
        except (ReviewError, TransactionError, OSError, ValueError) as exc:
            return review_error("review_failed", str(exc), exit_code=2)

        report_payload = report.to_dict()
        # Keep a compact compatibility view for clients of the v0.0.7
        # ``review`` alias while making the complete signed report available
        # under the explicit ``report`` key.
        compatibility = {
            "session": report.session.get("id"),
            "issues": report.session.get("issues", []),
            "plan": report.plan,
            "references": report.references,
            "checks": [item.to_dict() for item in report.checks],
            "findings": [item.to_dict() for item in report.findings],
            "hunks": [item.to_dict() for item in report.hunks],
            "rollback": report.rollback,
            "conflicts": list(report.conflicts),
            "audit_complete": report.audit_complete,
        }
        aliases = {
            "transaction_id": report.rollback.get("transaction_id") or transaction_id,
            "rollback_available": bool(report.rollback.get("available")),
            "report": report_payload,
            "review": compatibility,
            "type": "review_report",
        }
        if artifact_meta is not None:
            aliases["artifact"] = artifact_meta
        if machine_json:
            if report.exit_code == 0:
                envelope = _machine_envelope(command_name, "review_report", True, data=report_payload, exit_code=0, **_compat_aliases(aliases))
            else:
                # A report containing findings/conflicts is evidence of a
                # failed review, not a successful command carrying ``ok:false``
                # in its data.  Keep the complete bounded report under error
                # details and expose legacy aliases additively.
                error_code = "review_conflict" if report.exit_code == 3 else "review_failed"
                envelope = _machine_error(
                    command_name,
                    error_code,
                    "review report did not pass",
                    exit_code=report.exit_code,
                    details={"report": report_payload},
                )
                envelope.update(_compat_aliases(aliases))
            _emit_machine(envelope)
        else:
            session_id = report.session.get("id") or "none"
            selected_tx = report.rollback.get("transaction_id") or transaction_id
            print(f"review={report.report_id} session={session_id} transaction={selected_tx} status={'pass' if report.exit_code == 0 else 'fail'}")
            print(f"audit_complete={report.audit_complete} checks={len(report.checks)} findings={len(report.findings)} hunks={len(report.hunks)}")
            for check in report.checks:
                print(f"check {check.check_id}: {check.status} ({check.message})")
            for finding in report.findings[:50]:
                location = f" {finding.path}:{finding.line}" if finding.path and finding.line else (f" {finding.path}" if finding.path else "")
                print(f"finding {finding.severity}{location}: {finding.message}")
            print(f"rollback_available={bool(report.rollback.get('available'))}")
            if report.conflicts:
                print("conflicts: " + "; ".join(report.conflicts[:20]), file=sys.stderr)
            if artifact_meta:
                print(f"artifact={artifact_meta['path']} action={artifact_meta['action']}")
        # The report's bounded, deterministic exit code is the contract for
        # both human and machine invocations.
        return report.exit_code

    if command in {"transaction", "rollback"}:
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = command
        exit_code = 0
        try:
            store = TransactionStore(guard)
            transaction_id = args.transaction_id
            if command == "rollback" or getattr(args, "execute", False):
                manifest = store.latest() if transaction_id == "latest" else store.load(transaction_id)
                api_key = os.getenv(settings.api_key_env or "FORGECODE_API_KEY", "")
                approval = InteractiveApproval(auto_approve=getattr(args, "auto_approve", False), output_fn=_approval_output(machine_json), prompt_to_output=machine_json, secrets=[api_key])
                if not getattr(args, "execute", False) and command != "rollback":
                    payload = store.review(transaction_id)
                else:
                    undone = store.undo(manifest.transaction_id, approval=approval, run_id=uuid.uuid4().hex)
                    payload = {"ok": True, "transaction_id": undone.transaction_id, "parent_transaction_id": manifest.transaction_id, "state": undone.state}
            else:
                payload = store.review(transaction_id)
        except (TransactionError, OSError, ValueError) as exc:
            payload = {"ok": False, "error": _redact_display(str(exc))}
            exit_code = 3 if "conflict" in str(exc).lower() or "hash" in str(exc).lower() else 2
            if machine_json:
                code = "transaction_conflict" if exit_code == 3 else "transaction_unavailable"
                envelope = _machine_error(command_name, code, payload["error"], exit_code=exit_code)
                envelope["type"] = "transaction"
                _emit_machine(envelope)
            else:
                print("transaction unavailable: " + payload["error"], file=sys.stderr)
            return exit_code
        transaction_conflicts = payload.get("conflicts") or payload.get("rollback_conflicts")
        transaction_ok = bool(payload.get("ok", True)) and not bool(transaction_conflicts)
        if machine_json:
            if transaction_ok:
                envelope = _machine_envelope(command_name, "transaction", True, data=payload, exit_code=0)
                if isinstance(payload, dict):
                    envelope.update(_compat_aliases({key: value for key, value in payload.items() if key not in {"ok", "data", "error"}}))
            else:
                conflicts = transaction_conflicts or []
                if not isinstance(conflicts, list):
                    conflicts = [str(conflicts)] if conflicts else []
                envelope = _machine_error(command_name, "transaction_conflict", "transaction contains conflicts", details={"conflicts": conflicts[:100]}, exit_code=3)
                envelope.update(_compat_aliases({key: value for key, value in payload.items() if key not in {"ok", "data", "error"}}))
            envelope["type"] = "transaction"
            _emit_machine(envelope)
        else:
            print(f"transaction={payload.get('transaction_id')} state={payload.get('state')} rollback_available={payload.get('rollback_available', payload.get('ok', False))}")
            if payload.get("preview"):
                print(payload["preview"])
            if payload.get("conflicts"):
                print("conflicts: " + "; ".join(payload["conflicts"]))
        return 0 if transaction_ok else 3

    if command in {"chat", "start"}:
        # The REPL is scriptable by design: callers may pipe lines through
        # stdin, while tests can inject a stream through ``InteractiveSession``.
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        initial_prompt = " ".join(args.prompt).strip()
        api_key = os.getenv(settings.api_key_env or "FORGECODE_API_KEY", "")
        new_run_id = uuid.uuid4().hex
        try:
            session_path = _resolve_session_reference(guard, workspace, args.session) if args.session else _new_session_path(guard, new_run_id)
        except (OSError, ValueError, SessionFormatError) as exc:
            message = _redact_display(str(exc), [api_key])
            if machine_json:
                _emit_machine(_machine_error("chat", "invalid_session", message, exit_code=2) | {"type": "chat"})
            else:
                print(f"invalid chat session: {message}", file=sys.stderr)
            return 2
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
                if machine_json:
                    envelope = _machine_error("chat", "session_not_new", message, exit_code=3)
                    envelope.update(_compat_aliases({"type": "chat", "message": message}))
                    _emit_machine(envelope)
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
                if machine_json:
                    if getattr(args, "_legacy_json_chat_existing", False):
                        _emit_legacy_error(
                            "chat",
                            "session_not_new",
                            payload["message"],
                            exit_code=3,
                            session=payload["session"],
                            events=payload["events"],
                            issues=payload["issues"],
                        )
                        return 3
                    envelope = _machine_error("chat", "session_not_new", payload["message"], exit_code=3)
                    envelope.update(_compat_aliases({"type": "chat", "session": payload["session"], "events": payload["events"], "issues": payload["issues"], "message": payload["message"]}))
                    _emit_machine(envelope)
                else:
                    print(payload["message"], file=sys.stderr)
                return 3
        session = SessionStore(session_path, secrets=[api_key], run_id=new_run_id, mode=args.mode)
        session.append("tool_policy", tool_policy_summary, mode=args.mode)
        transaction_store = TransactionStore(guard)
        context_index = ContextIndex(guard)
        hook_registry = HookRegistry()
        def _audit_hook(event_payload: dict[str, Any]) -> None:
            try:
                session.append("hook_event", {"event": event_payload}, mode=args.mode)
            except Exception:
                # Hook observability must not turn a successful tool into a
                # side effect bypass or crash; AgentLoop records audit I/O
                # failures separately.
                return
        hook_registry.register(Hook("session-audit", "*", _audit_hook, failure_policy="observe_only", timeout_seconds=1.0))
        configured_approval = settings.effective.approval if settings.effective else "interactive"
        approval = None
        state = {"mode": args.mode, "last": None, "plan": None, "plan_targets": (), "reference_specs": (), "rules_fingerprint": "", "reference_fingerprint": "", "index_fingerprint": "", "last_message": "", "last_verification": None}
        active_service: RunService | None = None
        active_service_lock = threading.RLock()
        controller_holder: dict[str, InteractiveRunController | None] = {"value": None}
        shortcut_control: dict[str, Any] = {"token": None, "pause": None, "fingerprint": None}
        output_lock = threading.RLock()
        approval_lines: queue.Queue[str] = queue.Queue()
        approval_waiting = threading.Event()
        def approval_input(prompt: str = "") -> str:
            if threading.current_thread() is threading.main_thread():
                return input(prompt)
            approval_waiting.set()
            if prompt and not machine_json:
                print(prompt, end="", flush=True)
            try:
                return approval_lines.get(timeout=300.0)
            except queue.Empty:
                return ""
            finally:
                approval_waiting.clear()
        bypass = state["mode"] == AgentMode.BYPASS.value
        approval = AllowAllApproval() if bypass else (DenyAllApproval() if configured_approval == "deny" and not (args.auto_approve or args.demo) else InteractiveApproval(auto_approve=args.auto_approve or args.demo or configured_approval == "auto", input_fn=approval_input, output_fn=_approval_output(machine_json), prompt_to_output=machine_json, secrets=[api_key]))
        if args.demo and not any((workspace / name).exists() for name in ("demo_calculator.py", "demo_config.json")):
            # The demo fixture is a bounded, explicit offline setup action.
            # Prepare it before reading stdin so legacy scripted chat clients
            # can inspect/edit the file immediately after dispatch returns,
            # even though real runs now execute on the controller worker.
            _prepare_demo_workspace(registry, guard, task=args.demo_task)

        def run_message(message: str) -> Any:
            nonlocal active_service
            if not message.strip():
                return {"error": "message must not be empty"}
            try:
                shortcut = parse_command_shortcut(message)
                if shortcut is not None:
                    # Reject/execute at the executor boundary before resolving
                    # prompt context, so every valid shortcut gets exactly one
                    # bounded audit event even when project diagnostics fail.
                    return execute_shortcut(shortcut)
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
                    _prepare_demo_workspace(registry, guard, task=args.demo_task)
                expected_rule_fingerprint = rules.fingerprint
                expected_reference_fingerprint = references.fingerprint
                reference_specs = tuple(item.reference for item in references.items)
                state["last_message"] = message
                state["plan_targets"] = tuple(target_paths)
                state["reference_specs"] = reference_specs
                state["rules_fingerprint"] = expected_rule_fingerprint
                state["reference_fingerprint"] = expected_reference_fingerprint
                if args.demo:
                    provider = DemoProvider(args.demo_task)
                else:
                    effective = settings.effective
                    provider = _build_provider(effective)
                enriched = message
                if rules.text:
                    enriched += "\n\nProject rules (untrusted context):\n" + rules.render(20_000)
                if references.items:
                    enriched += "\n\nExplicit references:\n" + references.render(40_000)
                try:
                    index_report = context_index.ensure()
                    terms = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{3,}", message)][:3]
                    matches = []
                    for term in terms:
                        matches.extend(context_index.search(term, max_results=4))
                    unique_matches = {(item.path, item.line): item for item in matches}
                    selected = sorted(unique_matches.values(), key=lambda item: (item.path, item.line))[:12]
                    if selected:
                        enriched += "\n\nIndexed repository context (untrusted; digest-checked):\n" + "\n\n".join(f"{item.path}:{item.line} digest={item.digest[:16]} reason={item.reason}\n{item.snippet[:1_500]}" for item in selected)[:12_000]
                    session.append("context_index", {"path": index_report.path, "fingerprint": index_report.fingerprint, "files": index_report.files, "added": index_report.added, "updated": index_report.updated, "removed": index_report.removed, "omitted": index_report.omitted, "selected": [item.path for item in selected]}, mode=state["mode"])
                except (ContextIndexError, OSError, ValueError) as exc:
                    session.append("context_index_error", {"error": type(exc).__name__}, mode=state["mode"], error_code="context_index_error")
                demo_verify = "python -B -m pytest -q test_demo_calculator.py" if args.demo_task == "calculator" else "python -B -m pytest -q test_demo_config.py"
                service_config = AgentConfig(
                    verification_command=demo_verify if args.demo and state["mode"] == "act" else None,
                    max_verification_attempts=1,
                    compact_threshold_chars=settings.effective.compact_threshold_chars if settings.effective else None,
                )
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

                service = RunService(provider, registry, guard, session, service_config, settings.effective, approval, transaction_store, state["plan"].plan_id if state["plan"] else None, "task-1" if state["plan"] else None, expected_rule_fingerprint, state["plan"].evidence_fingerprint() if state["plan"] else "", expected_config_fingerprint, revalidate_context, hook_registry)
                service.enable_interactive_controls()
                with active_service_lock:
                    active_service = service
                controller = controller_holder["value"]
                if controller is not None:
                    # A control command can arrive while references/rules and
                    # the service are still being assembled.  Apply that
                    # intent immediately after the loop becomes addressable.
                    controller.flush_pending_controls()
                try:
                    started_at = time.monotonic()
                    tool_steps = 0
                    current_phase = ""
                    file_snapshots: dict[str, str] = {}
                    def progress_event(kind: str, payload: dict[str, Any]) -> None:
                        nonlocal tool_steps, current_phase
                        if machine_json:
                            return
                        labels = {"tool_call": "▸", "tool_result": "✓" if payload.get("ok") else "✗", "verification_result": "✓" if payload.get("ok") else "✗", "command_result": "✓" if payload.get("ok") else "✗", "command_timeout": "✗", "mode": "•", "model_message": "◆", "model_progress": "…"}
                        if kind not in labels:
                            return
                        tool = str(payload.get("tool") or "")
                        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                        detail = str(arguments.get("path") or arguments.get("command") or payload.get("command") or payload.get("mode") or "")
                        verbs = {"read_file": "Read", "write_file": "Write", "apply_patch": "Apply patch", "run_command": "Run"}
                        text = f"{verbs.get(tool, tool or kind)} {detail}".strip()
                        color = "\x1b[32m" if labels[kind] == "✓" else ("\x1b[31m" if labels[kind] == "✗" else "\x1b[36m")
                        with output_lock:
                            elapsed = time.monotonic() - started_at
                            phase = ""
                            if kind in {"model_progress", "model_message"}:
                                phase = "Understand" if int(payload.get("step", 0)) == 0 else "Inspect"
                            elif tool in {"read_file", "search", "list_files", "workspace_summary", "repository_map"}:
                                phase = "Inspect"
                            elif tool in {"write_file", "apply_patch"}:
                                phase = "Modify"
                            elif kind in {"verification_result", "command_result", "command_timeout"} or tool == "run_command":
                                phase = "Verify"
                            if phase and phase != current_phase:
                                current_phase = phase
                                print(f"\n\x1b[1;36m─── {phase} ───\x1b[0m")
                            if kind == "model_progress":
                                turn = int(payload.get("step", 0)) + 1
                                print(f"\x1b[2K\r\x1b[35m… assistant turn {turn}  ({elapsed:.1f}s)\x1b[0m {payload.get('message', '')}  \x1b[90m[{tool_steps} tool steps]\x1b[0m")
                                redraw_input_bar()
                                return
                            if kind == "model_message":
                                content = str(payload.get("content") or "").strip()
                                if content:
                                    turn = int(payload.get("step", 0)) + 1
                                    print(f"\x1b[2K\r\x1b[35m◆ assistant turn {turn}  ({elapsed:.1f}s)\x1b[0m")
                                    print(content)
                                redraw_input_bar()
                                return
                            print(f"\x1b[2K\r{color}{labels[kind]} [{tool_steps + (1 if kind == 'tool_call' else 0)}] {text}  ({elapsed:.1f}s)\x1b[0m")
                            if kind == "tool_call":
                                tool_steps += 1
                            if kind == "tool_result" and tool == "read_file" and payload.get("ok"):
                                path = str(payload.get("metadata", {}).get("path") or arguments.get("path") or "")
                                output = str(payload.get("output") or "")
                                file_snapshots[path] = output
                                print("  \x1b[100;97m file content \x1b[0m")
                                for line_number, line in enumerate(output.splitlines()[:24], 1):
                                    print(f"  \x1b[100;37m{line_number:>3} │ {line[:240]}\x1b[0m")
                                if len(output.splitlines()) > 24:
                                    print(f"  \x1b[100;90m… {len(output.splitlines()) - 24} more lines\x1b[0m")
                            if kind == "tool_result" and tool in {"run_command", "search"} and payload.get("ok"):
                                output = str(payload.get("output") or "").strip()
                                if output:
                                    print("  \x1b[100;97m output \x1b[0m")
                                    for line in output.splitlines()[:12]:
                                        print(f"  \x1b[100;37m{line[:240]}\x1b[0m")
                            if kind == "tool_call" and tool in {"write_file", "apply_patch"}:
                                preview = arguments.get("content") or arguments.get("patch")
                                if isinstance(preview, str):
                                    path = str(arguments.get("path") or "")
                                    before = file_snapshots.get(path, "").splitlines()
                                    after = preview.splitlines() if tool == "write_file" else preview.splitlines()
                                    lines = list(difflib.unified_diff(before, after, lineterm="")) if before else [("+ " + line) for line in after]
                                    print("  \x1b[100;97m file preview \x1b[0m")
                                    for line in lines[:80]:
                                        line_color = "\x1b[32m" if line.startswith("+") and not line.startswith("+++") else ("\x1b[31m" if line.startswith("-") and not line.startswith("---") else "\x1b[90m")
                                        print(f"  \x1b[100m{line_color}{line}\x1b[0m")
                            if kind == "tool_result" and isinstance(payload.get("metadata"), dict):
                                metadata = payload["metadata"]
                                changed_path = metadata.get("path") or arguments.get("path")
                                if tool in {"write_file", "apply_patch"} and changed_path:
                                    before_exists = str(changed_path) in file_snapshots
                                    status_code = "M" if before_exists else "A"
                                    print(f"  \x1b[1;33m{status_code} {changed_path}\x1b[0m")
                                diff = payload["metadata"].get("diff")
                                if isinstance(diff, str):
                                    for line in diff[:4_000].splitlines()[:40]:
                                        line_color = "\x1b[32m" if line.startswith("+") else ("\x1b[31m" if line.startswith("-") else "\x1b[90m")
                                        print(f"  {line_color}{line}\x1b[0m")
                            redraw_input_bar()
                    result = asyncio.run(service.execute(enriched, mode=state["mode"], secrets=(api_key,) if api_key else (), on_event=progress_event))
                finally:
                    with active_service_lock:
                        if active_service is service:
                            active_service = None
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
                payload = {"stopped_reason": result.stopped_reason, "state": result.state, "succeeded": result.succeeded, "verification_ok": result.verification_ok, "run_id": result.run_id, "duration_seconds": round(time.monotonic() - started_at, 3), "tool_steps": tool_steps}
                if result.error:
                    payload["error"] = _redact_display(result.error, [api_key])
                # Interactive callers need to see the assistant's actual
                # response, not only the lifecycle status envelope.
                final_messages = [item for item in result.messages if item.role == "assistant" and item.content]
                if final_messages:
                    payload["message"] = _redact_display(final_messages[-1].content, [api_key])[:16_000]
                return payload
            except ShortcutParseError as exc:
                return {"accepted": False, "shortcut": True, "error": str(exc), "code": exc.code}
            except (ProviderError, ValueError, OSError) as exc:
                return {"error": _redact_display(str(exc), [api_key])}

        def execute_shortcut(shortcut: CommandShortcut) -> Any:
            """Execute a validated ``!``/``!!`` command through ShellTool.

            This helper intentionally does not call ``subprocess``.  It uses
            the production registry so workspace checks, risk classification,
            approval, timeout, cancellation and output redaction remain shared
            with AgentLoop tool calls.
            """
            command_fingerprint = hashlib_lib.sha256(shortcut.command.encode("utf-8", errors="replace")).hexdigest()
            if state["mode"] not in {AgentMode.ACT.value, AgentMode.BYPASS.value}:
                session.append(
                    "command_shortcut",
                    {"shortcut": shortcut.prefix, "kind": shortcut.kind, "command_fingerprint": command_fingerprint, "ok": False, "risk": "unknown", "approval": "not_requested", "cancelled": False, "timed_out": False, "truncated": False},
                    mode=state["mode"],
                    outcome="rejected",
                    error_code="mode_denied",
                )
                return {"accepted": False, "shortcut": shortcut.prefix, "command_fingerprint": command_fingerprint, "error": "command shortcuts require act or bypass mode", "code": "mode_denied"}
            token = CancellationToken()
            pause_event = threading.Event()
            shortcut_control.update({"token": token, "pause": pause_event, "fingerprint": command_fingerprint})
            controller = controller_holder.get("value")
            if controller is not None:
                control_snapshot = controller.snapshot()
                if control_snapshot.get("cancellation_requested"):
                    token.cancel("interactive cancel")
                elif control_snapshot.get("pause_requested"):
                    pause_event.set()
            expected_rules = state.get("rules_fingerprint", "")
            expected_references = state.get("reference_fingerprint", "")
            reference_specs = tuple(state.get("reference_specs", ()))
            target_paths = tuple(state.get("plan_targets", ()))

            # A caller may start ``chat --mode act`` without first creating an
            # interactive plan.  Establish the current read-only fingerprints
            # as the baseline, while still revalidating them immediately
            # before the command side effect.
            if not expected_rules:
                current_rules = RuleEngine(guard).discover(target_paths)
                if current_rules.has_errors:
                    shortcut_control.update({"token": None, "pause": None, "fingerprint": None})
                    session.append(
                        "command_shortcut",
                        {"shortcut": shortcut.prefix, "kind": shortcut.kind, "command_fingerprint": command_fingerprint, "ok": False, "risk": "unknown", "approval": "not_requested", "cancelled": False, "timed_out": False, "truncated": False},
                        mode=state["mode"],
                        outcome="failed",
                        error_code="context_source_error",
                    )
                    return {"accepted": False, "shortcut": shortcut.prefix, "error": "project rules contain fatal diagnostics", "code": "context_source_error"}
                expected_rules = current_rules.fingerprint

            def revalidate_shortcut() -> bool | str:
                try:
                    if reference_specs:
                        latest_references = ReferenceResolver(guard).resolve(reference_specs)
                        if latest_references.has_errors or latest_references.fingerprint != expected_references:
                            return "explicit referenced context changed after planning"
                    latest_rules = RuleEngine(guard).discover(target_paths)
                    if latest_rules.has_errors or latest_rules.fingerprint != expected_rules:
                        return "project rules changed after planning"
                    return True
                except Exception as exc:
                    return f"shortcut context validation failed: {type(exc).__name__}"

            def pause_wait() -> None:
                while pause_event.is_set() and not token.is_cancelled():
                    threading.Event().wait(0.01)

            def approval_observer(tool_name: str, arguments: dict[str, Any], approved: bool) -> None:
                session.append(
                    "shortcut_approval",
                    {
                        "shortcut": shortcut.prefix,
                        "tool": tool_name,
                        "approved": bool(approved),
                        "command_fingerprint": command_fingerprint,
                        "risk": arguments.get("_risk"),
                        "risk_reasons": arguments.get("_risk_reasons", ()),
                    },
                    mode=state["mode"],
                    outcome="approved" if approved else "denied",
                    error_code=None if approved else "approval_denied",
                )

            try:
                result = registry.execute(
                    "run_command",
                    {"command": shortcut.command},
                    ToolContext(
                        guard,
                        approval,
                        approval_observer=approval_observer,
                        mode=AgentMode.ACT,
                        secrets=(api_key,) if api_key else (),
                        cancellation_token=token,
                        transaction_store=transaction_store,
                        run_id=session.run_id,
                        plan_id=state["plan"].plan_id if state.get("plan") else None,
                        plan_fingerprint=state["plan"].evidence_fingerprint() if state.get("plan") else "",
                        rules_fingerprint=expected_rules,
                        pre_side_effect_check=revalidate_shortcut,
                        pause_wait=pause_wait,
                    ),
                )
                metadata = result.metadata if isinstance(result.metadata, dict) else {}
                # A pause arriving while the subprocess is running takes
                # effect at this command-result boundary before any model
                # follow-up is created. Cancellation remains fail-closed.
                pause_wait()
                safe_metadata = {
                    key: value
                    for key, value in metadata.items()
                    if key not in {"command", "command_id"}
                }
                safe_metadata = redact_value(safe_metadata, (api_key,) if api_key else ())
                redacted_output = redact_text(result.output, (api_key,) if api_key else ())
                safe_output = redacted_output[:4_000]
                redacted_stdout = redact_text(str(metadata.get("stdout", "")), (api_key,) if api_key else ())
                redacted_stderr = redact_text(str(metadata.get("stderr", "")), (api_key,) if api_key else ())
                if isinstance(safe_metadata, dict):
                    # ShellTool metadata can retain its larger internal
                    # capture; shortcut payloads and model context stay at
                    # the shortcut's smaller, auditable bound.
                    safe_metadata["stdout"] = redacted_stdout[:4_000]
                    safe_metadata["stderr"] = redacted_stderr[:4_000]
                shortcut_truncated = bool(metadata.get("truncated", False)) or any(
                    len(value) > 4_000 for value in (redacted_output, redacted_stdout, redacted_stderr)
                )
                cancelled = metadata.get("error") == "cancelled" or token.is_cancelled()
                payload = {
                    "accepted": True,
                    "shortcut": shortcut.prefix,
                    "kind": shortcut.kind,
                    "command_fingerprint": command_fingerprint,
                    "ok": bool(result.ok) and not cancelled,
                    "output": safe_output,
                    "metadata": safe_metadata,
                    "exit_code": metadata.get("exit_code"),
                    "timed_out": bool(metadata.get("timed_out", False)),
                    "cancelled": cancelled,
                    "truncated": shortcut_truncated,
                }
                error_code = "cancelled" if cancelled else ((metadata.get("error") or "command_failed") if not result.ok else None)
                if error_code:
                    payload["error"] = str(error_code)[:128]
                    payload["code"] = str(error_code)[:128]
                session.append(
                    "command_shortcut",
                    {
                        "shortcut": shortcut.prefix,
                        "kind": shortcut.kind,
                        "command_fingerprint": command_fingerprint,
                        "ok": bool(result.ok),
                        "risk": metadata.get("risk"),
                        "risk_reasons": metadata.get("risk_reasons", ()),
                        "hard_blocked": bool(metadata.get("hard_blocked", False)),
                        "approval": metadata.get("approval"),
                        "exit_code": metadata.get("exit_code"),
                        "timed_out": bool(metadata.get("timed_out", False)),
                        "cancelled": payload["cancelled"],
                        "truncated": payload["truncated"],
                        "stdout": redacted_stdout[:4_000],
                        "stderr": redacted_stderr[:4_000],
                    },
                    mode=state["mode"],
                    outcome="completed" if result.ok else ("cancelled" if payload["cancelled"] else "failed"),
                    error_code=str(error_code)[:128] if error_code else None,
                )
                if shortcut.kind == "local" or payload["cancelled"] or metadata.get("error") in {"paused", "tool_unavailable"}:
                    return payload
                # Only the model-visible shortcut feeds the bounded result into
                # the existing AgentLoop.  The command text and raw metadata
                # are intentionally absent from this prompt.
                context_payload = json.dumps(
                    {"shortcut": "!", "ok": payload["ok"], "output": safe_output, "metadata": safe_metadata},
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                # The shortcut worker is finished before the model-visible
                # follow-up begins; controls now target the nested AgentLoop.
                shortcut_control.update({"token": None, "pause": None, "fingerprint": None})
                return run_message("A user command shortcut completed. Continue the task using this bounded result; do not repeat the command unless explicitly needed.\n" + context_payload)
            except (ProviderError, ValueError, OSError) as exc:
                message = _redact_display(str(exc), [api_key])
                session.append("command_shortcut", {"shortcut": shortcut.prefix, "kind": shortcut.kind, "command_fingerprint": command_fingerprint, "ok": False, "error": type(exc).__name__}, mode=state["mode"], outcome="failed", error_code="shortcut_failed")
                return {"accepted": False, "shortcut": shortcut.prefix, "command_fingerprint": command_fingerprint, "error": message, "code": "shortcut_failed"}
            finally:
                shortcut_control.update({"token": None, "pause": None, "fingerprint": None})

        def _active_service() -> RunService | None:
            with active_service_lock:
                return active_service

        def pause_active() -> Any:
            service = _active_service()
            if service is not None:
                return service.pause()
            pause_event = shortcut_control.get("pause")
            token = shortcut_control.get("token")
            if pause_event is not None and token is not None and not token.is_cancelled():
                pause_event.set()
                return {"paused": True, "pending": True, "message": "shortcut pause will apply at the next safe boundary"}
            return {"paused": False, "error": "no active worker"}

        def resume_active() -> Any:
            service = _active_service()
            if service is not None:
                return service.resume()
            pause_event = shortcut_control.get("pause")
            token = shortcut_control.get("token")
            if pause_event is not None and token is not None and not token.is_cancelled():
                pause_event.clear()
                return {"resumed": True, "pending": False, "message": "shortcut pause released"}
            return {"resumed": False, "error": "no active worker"}

        def cancel_active() -> Any:
            service = _active_service()
            if service is not None:
                return service.cancel()
            token = shortcut_control.get("token")
            if token is not None:
                first = token.cancel("interactive cancel")
                return {"cancelled": first, "message": "interactive cancel"}
            return {"cancelled": False, "error": "no active worker"}

        def persist_controller_event(kind: str, payload: dict[str, object]) -> None:
            try:
                session.append(kind, payload, mode=state["mode"])
            except Exception:
                # Controller telemetry is best effort; the AgentLoop still
                # owns the authoritative audit and marks incomplete writes.
                return

        def emit_interactive_result(value: object) -> None:
            """Render a worker result without contaminating JSON stdout."""
            with output_lock:
                if machine_json:
                    success = _interactive_success(value)
                    if success:
                        record = _machine_envelope("chat", "interactive_result", True, data=value if isinstance(value, dict) else {"value": value}, exit_code=0, type="interactive_result", payload=value)
                    else:
                        message = str(value.get("message") or value.get("error") or "interactive command failed") if isinstance(value, dict) else str(value)
                        code = _interactive_error_code(value)
                        record = _machine_error("chat", code, message, exit_code=1)
                        record.update({"type": "interactive_result", "payload": value})
                    _emit_machine(record)
                else:
                    rendered = _human_result(value)
                    if rendered:
                        if not machine_json:
                            # Remove the active input line before appending a
                            # response; the bar is redrawn immediately below.
                            print("\x1b[2K\r", end="")
                        print("\n" + rendered + "\n")
                        if not machine_json:
                            redraw_input_bar()

        def interactive_output(text: str) -> None:
            with output_lock:
                # Keep asynchronous model output above the persistent prompt.
                print("\x1b[2K\r", end="")
                print(text)
                redraw_input_bar()

        def redraw_input_bar() -> None:
            """Draw the persistent human input area at the current bottom."""
            if machine_json:
                return
            print(f"\x1b[40;97m╭─ forgecode │ {state['mode']}\n╰─❯ \x1b[0m", end="", flush=True)

        def model_command(model_args: list[str]) -> Any:
            nonlocal settings
            action = model_args[0] if model_args else "show"
            try:
                raw = ConfigLoader(workspace)._read_file()
                profiles = raw.get("profiles", {}) if isinstance(raw, dict) else {}
                names = ["default", *sorted(name for name in profiles if isinstance(name, str) and name != "default")]
                if action == "list":
                    return {"profiles": [{"name": name, "selected": name == settings.profile} for name in names]}
                if action == "show":
                    selected = settings.profile
                    config = ConfigLoader(workspace).load(profile=selected)
                    return {"model_status": True, "profile": selected, "provider": config.provider, "model": config.model, "base_url": config.base_url, "configured": bool(config.model)}
                if action == "select":
                    selected = model_args[1]
                    with active_service_lock:
                        busy = active_service is not None or bool(controller_holder["value"] and controller_holder["value"].active)
                    if busy:
                        session.append("profile_switch_rejected", {"from": settings.profile, "to": selected, "reason": "worker_active"}, mode=state["mode"], outcome="rejected", error_code="worker_active")
                        return {"error": "profile switch unavailable while worker is active", "code": "worker_active", "profile": settings.profile}
                    config = ConfigLoader(workspace).load(profile=selected)
                    previous = settings.profile
                    settings = Settings(workspace=workspace, model=config.model, api_key_env=config.api_key_env, base_url=config.base_url, profile=config.profile, effective=config)
                    session.append("profile_switch", {"from": previous, "to": selected, "capabilities": {"streaming": config.streaming, "provider": config.provider}, "reason": "interactive selection"}, mode=state["mode"])
                    return {"profile": selected, "previous_profile": previous, "streaming": config.streaming, "provider": config.provider}
                return {"error": "usage: /model [list|show|select <name>]"}
            except (ConfigError, OSError, ValueError) as exc:
                return {"error": _redact_display(str(exc), [api_key])}

        def connect_command(connect_args: list[str]) -> Any:
            """Configure a provider for this chat process without persisting secrets."""
            nonlocal settings, api_key
            effective = settings.effective
            if connect_args:
                provider = connect_args[0]
            else:
                # In the TTY use a modal selector like OpenCode's provider
                # picker; retain a numbered fallback for pipes/non-TTY use.
                try:
                    from prompt_toolkit.shortcuts import radiolist_dialog
                    values = [(name, f"{name} — {PROVIDER_CATALOG[name]['base_url']}") for name in SUPPORTED_PROVIDERS]
                    provider = radiolist_dialog(title="ForgeCode · Connect", text="Select a provider (Esc cancels)", values=values).run() or ""
                except (ImportError, EOFError, KeyboardInterrupt):
                    print("Available providers:", flush=True)
                    for index, name in enumerate(SUPPORTED_PROVIDERS, 1):
                        catalog = PROVIDER_CATALOG[name]
                        marker = " (local)" if name == "ollama" else ""
                        print(f"  {index}. {name}{marker} — {catalog['base_url']}", flush=True)
                    selection = input("Provider (name or number): ").strip()
                    provider = SUPPORTED_PROVIDERS[int(selection) - 1] if selection.isdigit() and 1 <= int(selection) <= len(SUPPORTED_PROVIDERS) else selection
                if not provider:
                    return {"error": "provider selection cancelled", "code": "connect_cancelled"}
            if provider not in SUPPORTED_PROVIDERS:
                return {"error": f"unsupported provider: {provider}", "code": "unsupported_provider", "available": list(SUPPORTED_PROVIDERS)}
            defaults = PROVIDER_CATALOG[provider]
            print(f"Connect model ({provider})", flush=True)
            base_url = input(f"API endpoint [{defaults['base_url']}]: ").strip() or defaults["base_url"]
            # OpenCode resolves model IDs from the live models.dev catalog
            # (models.opencode.ai), rather than embedding a stale hand-picked
            # list.  Fetch the same catalog with a short timeout for the
            # picker; users can still enter a custom ID when offline.
            model_choices: tuple[str, ...] = ()
            try:
                request = urllib.request.Request("https://models.opencode.ai/api.json", headers={"User-Agent": "forgecode"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    catalog = json.loads(response.read().decode("utf-8"))
                catalog_provider = "openai" if provider == "openai-compatible" else provider
                provider_data = catalog.get(catalog_provider, {}) if isinstance(catalog, dict) else {}
                models = provider_data.get("models", {}) if isinstance(provider_data, dict) else {}
                if isinstance(models, dict):
                    model_choices = tuple(sorted(str(model_id) for model_id, item in models.items() if isinstance(item, dict) and item.get("status") != "deprecated"))[:200]
            except (OSError, ValueError, urllib.error.URLError):
                model_choices = ()
            model = ""
            try:
                from prompt_toolkit.shortcuts import radiolist_dialog
                from prompt_toolkit.styles import Style
                values = [(item, item) for item in model_choices] + [("__custom__", "Enter another model ID…")]
                picker_style = Style.from_dict({"dialog": "bg:#16181d #e6e6e6", "dialog.body": "bg:#16181d #e6e6e6", "dialog.frame": "bg:#16181d #e6e6e6", "dialog shadow": "bg:#000000", "radio-selected": "#7dd3fc bold", "radio": "#e6e6e6"})
                selected_model = radiolist_dialog(title=f"ForgeCode · {provider}", text="Select a model (Esc cancels)", values=values, style=picker_style).run()
                if selected_model == "__custom__":
                    model = input("Model ID (required): ").strip()
                else:
                    model = selected_model or ""
            except (ImportError, EOFError, KeyboardInterrupt):
                print("Known model IDs:", ", ".join(model_choices) if model_choices else "catalog unavailable; use the provider's exact model ID", flush=True)
                model = input("Model ID (required): ").strip()
            if provider not in SUPPORTED_PROVIDERS:
                return {"error": f"unsupported provider: {provider}", "code": "unsupported_provider"}
            if not base_url or not model:
                return {"error": "API endpoint URL and model name are required", "code": "connect_incomplete"}
            requires_key = provider_requires_credential(provider)
            if requires_key:
                entered = input(f"API key ({defaults['api_key_env']}): ").strip() or os.getenv(defaults["api_key_env"], "")
                if not entered:
                    return {"error": "API key must not be empty", "code": "credential_missing"}
                # Keep the credential process-local and never write it to config/session.
                api_key = entered
                os.environ[defaults["api_key_env"]] = entered
                try:
                    _save_connection(workspace, endpoint=base_url, model=model, api_key=entered)
                except OSError as exc:
                    return {"error": f"could not save connection: {exc}", "code": "connect_persist_failed"}
                api_key_env = defaults["api_key_env"]
            else:
                api_key_env = effective.api_key_env if effective else "FORGECODE_API_KEY"
            if effective is not None:
                updated = replace(effective, provider=provider, model=model or None, base_url=base_url, api_key_env=api_key_env)
            else:
                updated = None
            settings = Settings(workspace=workspace, model=model or None, api_key_env=api_key_env, base_url=base_url, profile=settings.profile, effective=updated)
            return {"connected": True, "provider": provider, "model": model or None, "base_url": base_url, "api_key_env": api_key_env, "api_key_configured": (not requires_key) or bool(api_key), "storage": "workspace-local-ignored" if requires_key else "process-environment-only"}

        def clear_screen_command() -> Any:
            if not machine_json:
                print("\x1b[2J\x1b[H", end="", flush=True)
            return {"cleared": True}

        def status() -> Any:
            manifests = transaction_store.list(limit=20)
            controller = controller_holder["value"]
            return {"mode": state["mode"], "run_id": session.run_id, "transactions": len(manifests), "last_state": getattr(state["last"], "state", None), "latest_verification": state["last_verification"], "worker": controller.snapshot() if controller is not None else {"active": False}}

        def tools_command() -> Any:
            rows = []
            for definition in registry.definitions(state["mode"]):
                rows.append({"name": definition.name, "description": definition.description, "available": True, "side_effecting": definition.side_effecting})
            return {"tools_status": True, "tools": rows, "mode": state["mode"]}

        def login_command() -> Any:
            effective = settings.effective
            provider = effective.provider if effective else "openai-compatible"
            requires_key = provider_requires_credential(provider)
            return {"provider": provider, "profile": settings.profile, "api_key_env": effective.api_key_env if effective and requires_key else None, "configured": (not requires_key) or bool(effective and os.getenv(effective.api_key_env, "")), "credential": "required" if requires_key else "optional", "storage": "environment-only"}

        def plan_command(_args: list[str]) -> Any:
            state["mode"] = "plan"
            return {"mode": "plan", "plan": state["plan"].to_dict() if state["plan"] else None, "message": "planning mode; side effects are disabled"}

        def set_mode(mode: str) -> Any:
            nonlocal approval
            if mode in {"act", "bypass"}:
                if state.get("plan") is not None:
                    try:
                        if not TrustStore(workspace).status().get("trusted", False) and not args.demo:
                            return {"error": "workspace is not trusted; run `forgecode trust grant` before act mode", "code": "trust_required"}
                    except TrustError as exc:
                        return {"error": str(exc), "code": "trust_error"}
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
                    session.append("plan_approved", {"plan_id": state["plan"].plan_id, "revision": state["plan"].revision}, mode=mode)
            state["mode"] = mode
            if mode == AgentMode.BYPASS.value:
                approval = AllowAllApproval()
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

            result = registry.execute("run_command", {"command": command}, ToolContext(guard, approval, mode="act", secrets=(api_key,) if api_key else (), transaction_store=transaction_store, run_id=session.run_id, pre_side_effect_check=revalidate_verification_context, rules_fingerprint=expected_rule_fingerprint))
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
        def skills_command(skill_args: list[str]) -> Any:
            loader = SkillLoader(guard)
            discovered = loader.discover()
            registry_skills = SkillRegistry(discovered)
            if not skill_args:
                return {"skills": [item.to_dict() for item in discovered], "errors": list(loader.errors)}
            try:
                skill = registry_skills.get(skill_args[0])
            except SkillError as exc:
                return {"ok": False, "error": "unknown_skill", "message": _redact_display(str(exc), [api_key])}
            requested_approval = len(skill_args) > 1 and skill_args[1] == "--approve"
            if requested_approval and state["mode"] != "act" and skill.manifest.side_effect != "read_only":
                invocation = SkillInvocation(skill.manifest.id, skill.manifest.version, False, "side-effecting skills are unavailable in plan mode", "mode_denied", skill.manifest.side_effect)
            else:
                approved = False
                if requested_approval:
                    approved = approval.approve("skill", {"skill_id": skill.manifest.id, "version": skill.manifest.version, "side_effect": skill.manifest.side_effect})
                invocation = registry_skills.invoke(skill.manifest.id, executor=SkillExecutor(guard), approved=approved)
            try:
                session.append("skill_invocation", {"skill_id": invocation.skill_id, "version": invocation.version, "ok": invocation.ok, "error": invocation.error, "permission": invocation.permission, "approved": requested_approval}, mode=state["mode"], outcome="completed" if invocation.ok else "failed", error_code=invocation.error)
            except Exception:
                pass
            return invocation.to_dict()
        def quit_session() -> Any:
            worker_stopped = True
            controller = controller_holder["value"]
            if controller is not None and controller.active:
                # Request cooperative cancellation first and allow the
                # provider/tool boundary to observe it.  Only after this
                # bounded grace period do we use the shorter hard cleanup;
                # this prevents /quit from racing a late provider response
                # into a side-effecting tool dispatch.
                controller.cancel()
                worker_stopped = controller.join(30.0)
                if not worker_stopped:
                    worker_stopped = controller.stop(cancel=True, timeout=2.0)
                if not worker_stopped:
                    try:
                        session.append("recovery_required", {"reason": "interactive worker did not stop within bounded shutdown", "timeout_seconds": 2.0}, mode=state["mode"], outcome="unresolved", error_code="worker_unresolved")
                    except Exception:
                        pass
            try:
                last_result = state.get("last")
                checkpoint_state = "recovery_required" if not worker_stopped else (getattr(last_result, "state", None) or "created")
                checkpoint = Checkpoint.create(
                    guard,
                    run_id=session.run_id,
                    state=str(checkpoint_state),
                    # Checkpoint schema records executable modes only; bypass
                    # uses the same act lifecycle but skips approvals.
                    mode="act" if state["mode"] == "bypass" else state["mode"],
                    sequence=session.last_sequence,
                    verification=state.get("last_verification"),
                    plan_fingerprint=state["plan"].evidence_fingerprint() if state.get("plan") else "",
                    context_summary=state.get("last_message", "")[:8_000],
                    secrets=(api_key,) if api_key else (),
                )
                CheckpointStore(session.path.with_suffix(".checkpoint.json")).save(checkpoint)
                event = session.append("interactive_quit", {"state": checkpoint_state, "last_verification": state.get("last_verification"), "checkpoint_sequence": checkpoint.sequence}, mode=state["mode"], outcome="checkpointed" if worker_stopped else "recovery_required", error_code=None if worker_stopped else "worker_unresolved")
                return {"stopped": True, "checkpointed": True, "worker_stopped": worker_stopped, "recovery_required": not worker_stopped, "sequence": event.sequence}
            except Exception as exc:
                return {"stopped": True, "checkpointed": False, "worker_stopped": worker_stopped, "recovery_required": not worker_stopped, "error": f"checkpoint failed: {type(exc).__name__}"}

        def files_command(prefix: str = "") -> Any:
            try:
                index = ContextIndex(guard)
                index.ensure()
                return {"prefix": prefix, "results": list(index.complete(prefix)), "advisory": True}
            except (ContextIndexError, OSError, ValueError) as exc:
                return {"error": _redact_display(str(exc), [api_key])}

        def tree_command(_args: list[str]) -> Any:
            try:
                from .session_service import SessionService
                return SessionService(guard).tree()
            except (OSError, ValueError) as exc:
                return {"error": _redact_display(str(exc), [api_key])}

        controller = InteractiveRunController(
            start=run_message,
            on_result=emit_interactive_result,
            event_sink=persist_controller_event,
            pause_active=pause_active,
            resume_active=resume_active,
            cancel_active=cancel_active,
        )
        controller_holder["value"] = controller
        interactive = InteractiveSession(
            run_message,
            status=status,
            tools=tools_command,
            plan=plan_command,
            set_mode=set_mode,
            model=model_command,
            connect=connect_command,
            login=login_command,
            review=review,
            test=test_command,
            compact=compact,
            undo=undo_command,
            rules=lambda: RuleEngine(guard).discover().to_dict(),
            files=files_command,
            skills=skills_command,
            tree=tree_command,
            cancel=controller.cancel,
            pause=controller.pause,
            resume=controller.resume,
            quit=quit_session,
            output=interactive_output,
            raw_output=lambda text: print(text, end="", flush=True),
            input_bar=redraw_input_bar,
            clear_screen=clear_screen_command,
            approval_pending=approval_waiting.is_set,
            submit_approval=lambda line: approval_lines.put(line),
            json_mode=bool(getattr(args, "json", False)),
            jsonl_mode=bool(getattr(args, "jsonl", False)),
            controller=controller,
        )
        if machine_json:
            header_data = {"run_id": session.run_id, "workspace": ".", "mode": state["mode"], "profile": settings.profile, "rules": rules_count, "budget": settings.effective.context_budget_chars if settings.effective else 60_000}
            _emit_machine(_machine_envelope("chat", "interactive_header", True, data=header_data, exit_code=0, type="interactive_header", **_compat_aliases(header_data)))
        else:
            print(interactive.header(run_id=session.run_id, mode=state["mode"], rules_count=rules_count))
            print("╭──────────────────────────────╮")
            print("│   ◆ FORGECODE // READY ◆     │")
            print("│   local coding harness       │")
            print("╰──────────────────────────────╯\n")
            effective_model = settings.effective.model if settings.effective else settings.model
            print(f"\x1b[90mstatus  ready   mode: {state['mode']}   model: {effective_model or 'not configured'}\x1b[0m")
            print(f"\x1b[90mtools   {len(registry.names())} enabled   workspace: trusted\x1b[0m\n")
        if initial_prompt:
            initial_result = interactive.dispatch(initial_prompt)
            if initial_result is not None:
                if machine_json:
                    # ``InteractiveSession.run_stream`` emits subsequent
                    # responses in the same envelope format; initial prompt
                    # follows that contract as well.
                    success = _interactive_success(initial_result)
                    if success:
                        record = _machine_envelope("chat", "interactive_result", True, data=initial_result, exit_code=0, type="interactive_result", payload=initial_result)
                    else:
                        message = str(initial_result.get("message") or initial_result.get("error") or "interactive command failed") if isinstance(initial_result, dict) else str(initial_result)
                        code = _interactive_error_code(initial_result)
                        record = _machine_error("chat", code, message, exit_code=1)
                        record.update({"type": "interactive_result", "payload": initial_result})
                    _emit_machine(record)
                else:
                    print(initial_result)
        shutdown_unresolved = False
        try:
            stream = sys.stdin
            if bool(getattr(stream, "isatty", lambda: False)()) and not machine_json:
                run_prompt_ui(interactive, mode=lambda: state["mode"])
            else:
                interactive.run_stream(stream)
        except KeyboardInterrupt:
            controller.stop(cancel=True, timeout=2.0)
            session.append("state_transition", {"to": "cancelled", "reason": "user interruption"}, mode=state["mode"], error_code="cancelled")
            return 130
        finally:
            # EOF is a normal terminal boundary.  Give an in-flight worker a
            # bounded grace period to finish normally (important for a
            # piped/initial prompt), then request cancellation and record an
            # unresolved worker if it still cannot stop.
            if controller.active:
                controller.join(30.0)
            if controller.active:
                stopped = controller.stop(cancel=True, timeout=2.0)
                if not stopped:
                    shutdown_unresolved = True
                    session.append("recovery_required", {"reason": "interactive worker did not stop at EOF", "timeout_seconds": 2.0}, mode=state["mode"], outcome="unresolved", error_code="worker_unresolved")
        return 3 if shutdown_unresolved else 0

    if command == "doctor":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = "doctor"
        effective = settings.effective
        provider_health = {
            "provider": effective.provider if effective else "openai-compatible",
            "configured": bool(settings.model and (not provider_requires_credential(effective.provider if effective else "openai-compatible") or os.getenv(settings.api_key_env, ""))),
            "model": settings.model,
            "base_url": redact_text(settings.base_url),
            "streaming": effective.streaming if effective else "auto",
            "capabilities": {"tool_calling": True, "json_mode": False, "streaming": bool(effective and effective.streaming in {"on", "required"}), "max_input_chars": 4_000_000, "max_output_chars": 4_000_000},
        }
        provider_health["credential"] = "required" if provider_requires_credential(provider_health["provider"]) else "optional"
        if machine_json:
            # A model name alone is not a usable provider configuration; the
            # configured flag must agree with the offline health probe and
            # include the selected credential environment variable.
            data = {"version": __version__, "workspace": ".", "profile": settings.profile, "model": settings.model, "configured": provider_health["configured"], "config_sources": list(effective.sources) if effective else ["environment"], "streaming": effective.streaming if effective else "auto", "offline": bool(effective.offline) if effective else False, "telemetry": effective.telemetry if effective else "off", "tools": list(registry.names()), "provider_health": provider_health, "status": "ready"}
            envelope = _machine_envelope(command_name, "doctor", True, data=data, exit_code=0, **_compat_aliases(data))
            if machine_json:
                envelope["type"] = "doctor"
            _emit_machine(envelope)
            return 0
        print(f"ForgeCode v{__version__}")
        print("workspace: .")
        print(f"profile: {settings.profile}")
        print(f"model: {settings.model or 'not configured (framework-only mode)'}")
        print("config sources: " + ", ".join(effective.sources if effective else ("environment",)))
        print("tools: " + ", ".join(registry.names()))
        print(f"provider health: {'configured' if provider_health['configured'] else 'offline/unconfigured'}")
        print(f"offline: {bool(effective.offline) if effective else False} telemetry: {effective.telemetry if effective else 'off'}")
        print("status: ready")
        return 0

    if command == "tools":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = "tools"
        tool_data = [{"name": definition.name, "description": definition.description, "side_effecting": definition.side_effecting, "parameters": definition.parameters} for definition in registry.definitions()]
        if getattr(args, "json", False) and not getattr(args, "jsonl", False):
            # ``tools --json`` predates the envelope and is consumed by
            # clients that iterate the top-level array.  Keep that explicit
            # legacy spelling stable; ``--jsonl`` is the canonical envelope.
            print(jsonlib.dumps(tool_data, ensure_ascii=False, allow_nan=False))
            return 0
        if machine_json:
            # Both new machine transports use an object envelope.  The old
            # array remains available only through the explicit legacy mode
            # above.
            envelope = _machine_envelope(command_name, "tools", True, data={"tools": tool_data}, exit_code=0, tools=tool_data)
            envelope["type"] = "tools"
            _emit_machine(envelope)
            return 0
        for definition in registry.definitions():
            print(f"{definition.name}: {definition.description}")
        return 0

    if command in {"inspect", "map"}:
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = "inspect" if command == "inspect" else "map"
        try:
            repository = RepositoryMapBuilder(guard).build()
            context_plan = repository.plan_context(args.task, budget_chars=args.budget_chars)
        except (OSError, ValueError) as exc:
            if machine_json:
                _emit_machine(_machine_error(command_name, "inspect_failed", _redact_display(str(exc)), exit_code=2))
            else:
                print(f"inspect failed: {_redact_display(str(exc))}", file=sys.stderr)
            return 2
        inspect_payload = {"snapshot": repository.to_dict(), "context": {"selected_paths": list(context_plan.selected_paths), "omitted": context_plan.omitted, "budget_chars": context_plan.budget_chars, "rendered": context_plan.render()}}
        if machine_json:
            _emit_command_payload(args, command_name, "repository_map", inspect_payload, type_label="repository_map")
            return 0
        if not machine_json:
            print(context_plan.render())
            print(f"[map] files={len(repository.snapshot.files)} omitted={repository.snapshot.omitted} errors={len(repository.snapshot.errors)}")
        return 0

    if command == "sessions":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        if isinstance(args.limit, bool) or args.limit < 1 or args.limit > 200:
            if machine_json:
                _emit_machine(_machine_error("sessions", "invalid_limit", "limit must be between 1 and 200", exit_code=2))
            else:
                print("limit must be between 1 and 200", file=sys.stderr)
            return 2
        try:
            directory = guard.resolve(Path(".forgecode") / "sessions")
        except (OSError, ValueError):
            if machine_json:
                _emit_machine(_machine_error("sessions", "invalid_workspace", "session directory is outside workspace", exit_code=2))
            else:
                print("session directory is outside workspace", file=sys.stderr)
            return 2
        entries = sorted((path for path in directory.glob("*.jsonl") if path.is_file()), key=lambda path: path.name, reverse=True) if directory.is_dir() else []
        rows = []
        for path in entries:
            store = SessionStore(path)
            result = store.read_with_issues()
            last = result.events[-1] if result.events else None
            state = _last_session_state(result.events)
            effective_state = state or (last.payload.get("state") if last else None)
            if getattr(args, "state", None) and effective_state != args.state:
                continue
            rows.append({"id": path.stem, "path": guard.relative(path), "events": len(result.events), "issues": len(result.issues), "state": effective_state})
            if len(rows) >= args.limit:
                break
        if getattr(args, "json", False) and not getattr(args, "jsonl", False):
            # Preserve the v0.0.7 top-level array for existing session-list
            # consumers.  ``sessions --jsonl`` exposes the bounded envelope.
            print(jsonlib.dumps(rows, ensure_ascii=False, allow_nan=False))
        elif machine_json:
            # ``--json`` now follows the same object envelope as ``--jsonl``;
            # the historical array remains available under ``data.sessions``.
            _emit_command_payload(args, "sessions", "sessions", {"sessions": rows, "count": len(rows)}, aliases=True, type_label="sessions")
        else:
            if not rows:
                print("no sessions")
            for row in rows:
                print(f"{row['id']} events={row['events']} issues={row['issues']} state={row['state'] or 'unknown'}")
        return 0

    if command in {"diff", "status"}:
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = command
        try:
            path = _resolve_session_reference(guard, workspace, args.session, must_exist=True)
            result = SessionStore(path).read_with_issues()
        except (OSError, ValueError) as exc:
            if machine_json:
                _emit_machine(_machine_error(command_name, "session_unavailable", _redact_display(str(exc)), exit_code=2))
            else:
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
            if machine_json:
                if session_issues:
                    envelope = _machine_envelope(command_name, "error", False, error={"code": "session_issues", "message": "session contains validation issues", "details": {"issues": session_issues[:100]}}, exit_code=1)
                    envelope.update(_compat_aliases({"session": payload.get("session"), "transaction_id": payload.get("transaction_id"), "truncated": payload.get("truncated"), "rollback_available": payload.get("rollback_available"), "issues": session_issues}))
                    envelope["type"] = "diff"
                    _emit_machine(envelope)
                else:
                    _emit_command_payload(args, command_name, "diff", payload, aliases=True, type_label="diff")
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
        if machine_json:
            if result.issues or transaction_issues:
                envelope = _machine_envelope(command_name, "error", False, error={"code": "session_issues", "message": "session or transaction records contain validation issues", "details": {"session_issues": [issue.__dict__ for issue in result.issues][:100], "transaction_issues": transaction_issues[:100]}}, exit_code=1)
                envelope.update(_compat_aliases(payload))
                envelope["type"] = "status"
                _emit_machine(envelope)
            else:
                _emit_command_payload(args, command_name, "status", payload, aliases=True, type_label="status")
        else:
            print(f"session={path.stem} state={last_state} transactions={len(transactions)} issues={len(result.issues)}")
            for event in transactions[-20:]:
                print(f"{event.kind} transaction={event.payload.get('transaction_id')} ok={event.payload.get('ok')}")
            print(f"rollback_available={rollback_available}")
        return 0 if not result.issues and not transaction_issues else 1

    if command == "session":
        machine_json = bool(getattr(args, "json", False) or getattr(args, "jsonl", False))
        command_name = f"session {args.session_action}"
        try:
            if args.session_action == "tree":
                if isinstance(args.limit, bool) or args.limit < 1 or args.limit > 200:
                    if machine_json:
                        _emit_machine(_machine_error(command_name, "invalid_limit", "limit must be between 1 and 200", exit_code=2))
                    else:
                        print("limit must be between 1 and 200", file=sys.stderr)
                    return 2
                from .session_service import SessionService
                payload = SessionService(guard).tree(limit=max(1, min(200, args.limit)))
                if machine_json:
                    _emit_command_payload(args, command_name, "session_tree", payload, aliases=True, type_label="session_tree")
                else:
                    print(f"session tree nodes={len(payload['nodes'])} edges={len(payload['edges'])}")
                    for node in payload["nodes"]:
                        parent = node.get("parent_run_id") or "-"
                        print(f"{node['run_id']} parent={parent} state={node['state']} seq={node['sequence']} inspect_only={node['inspect_only']}")
                return 0
            if args.session_action == "import":
                try:
                    source = guard.resolve(args.artifact_path, must_exist=True)
                except (OSError, ValueError):
                    reference = args.artifact_path
                    if reference.suffix.lower() == ".jsonl" and not (workspace / reference).exists():
                        reference = Path(".forgecode") / "sessions" / reference.name
                    source = _resolve_session_reference(guard, workspace, reference, must_exist=True)
                if source.suffix.lower() != ".jsonl":
                    raise SessionFormatError("import requires a JSONL session artifact")
                source_store = SessionStore(source)
                read_result = source_store.read_with_issues()
                if read_result.issues or not read_result.events:
                    raise SessionFormatError("session artifact is invalid or empty")
                # Session exports are canonical JSONL.  Re-serializing the
                # validated events and requiring byte-for-byte equality makes
                # import integrity fail closed: a changed byte (including
                # whitespace, line endings, or an extra field) cannot be
                # silently accepted as a new artifact with a new digest.
                canonical = "".join(
                    jsonlib.dumps(asdict(event), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
                    for event in read_result.events
                ).encode("utf-8")
                if source.read_bytes() != canonical:
                    raise SessionFormatError("session artifact is not canonical or has been tampered with")
                run_ids = {event.run_id for event in read_result.events if event.run_id}
                sequences = [event.sequence for event in read_result.events]
                if len(run_ids) != 1 or sequences != sorted(set(sequences)):
                    raise SessionFormatError("session artifact has mixed run ids or duplicate sequences")
                child_id = uuid.uuid4().hex
                target = _new_session_path(guard, child_id)
                child = SessionStore(target, run_id=child_id, mode="plan")
                import hashlib
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                source_run_id = next(iter(run_ids))
                child.append("imported", {"source_digest": digest, "source_run_id": source_run_id, "source_sequence_start": min(sequences), "source_sequence_end": max(sequences), "parent_run_id": source_run_id, "parent_sequence": max(sequences), "parent_session": guard.relative(source), "event_count": len(read_result.events), "replay": False}, mode="plan")
                payload = {"run_id": child_id, "path": guard.relative(target), "source_digest": digest, "event_count": len(read_result.events), "replay": False}
                if machine_json:
                    _emit_command_payload(args, command_name, "session_import", payload, aliases=True, type_label="session_import")
                else:
                    print(f"imported run={child_id} source_digest={digest[:16]} replay=false")
                return 0
            session_path = _resolve_session_reference(guard, workspace, args.session_id, must_exist=True)
            store = SessionStore(session_path)
            if args.session_action == "export":
                output = store.export(max_chars=args.max_chars)
                issues = [issue.__dict__ for issue in store.last_read_issues]
                export_payload = {"path": guard.relative(session_path), "events_jsonl": output, "issues": issues}
                if machine_json:
                    if issues:
                        envelope = _machine_error(command_name, "session_issues", "session export is partial because the source contains validation issues", exit_code=1, details={"issues": issues[:100], "path": export_payload["path"]})
                        envelope.update(_compat_aliases(export_payload))
                        envelope["type"] = "session_export"
                        _emit_machine(envelope)
                    else:
                        _emit_command_payload(args, command_name, "session_export", export_payload, aliases=True, type_label="session_export")
                else:
                    print(output, end="")
                    if issues:
                        print("session export is partial because the source contains validation issues", file=sys.stderr)
                return 0 if not issues else 1
            if args.session_action == "inspect":
                rebuilt = SessionContextRebuilder().rebuild(store)
                payload = rebuilt.to_dict()
                if machine_json:
                    if rebuilt.conflicts:
                        envelope = _machine_error(command_name, "recovery_conflict", "session context contains recovery conflicts", exit_code=3, details={"conflicts": list(rebuilt.conflicts)[:100], "result": payload})
                        envelope.update(_compat_aliases(payload))
                        envelope["type"] = "session_inspect"
                        _emit_machine(envelope)
                    else:
                        _emit_command_payload(args, command_name, "session_inspect", payload, aliases=True, type_label="session_inspect")
                else:
                    print(f"session={session_path.stem} state={rebuilt.state} run_id={rebuilt.run_id} sequence={rebuilt.sequence} messages={len(rebuilt.messages)} conflicts={len(rebuilt.conflicts)}")
                return 3 if rebuilt.conflicts else 0
            if args.session_action == "compact":
                result = ContextCompactor(max_chars=args.max_chars).compact_store(store)
                if machine_json:
                    _emit_command_payload(args, command_name, "session_compact", result.to_dict(), aliases=True, type_label="session_compact")
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
                if machine_json:
                    _emit_command_payload(args, command_name, "session_fork", payload, aliases=True, type_label="session_fork")
                else:
                    print(f"forked run={child_id} parent={parent_run} session={guard.relative(child_path)}")
                return 0
            if args.session_action == "clone":
                read_result = store.read_with_issues()
                if read_result.issues or not read_result.events:
                    raise SessionFormatError("cannot clone an inconsistent or empty session")
                parent_run = read_result.events[0].run_id or store.run_id
                parent_sequence = args.sequence if args.sequence is not None else max(event.sequence for event in read_result.events)
                if parent_sequence < 0 or parent_sequence > max(event.sequence for event in read_result.events):
                    raise SessionFormatError("clone sequence is outside the session range")
                child_id = uuid.uuid4().hex
                child_path = _new_session_path(guard, child_id)
                child = SessionStore(child_path, run_id=child_id, mode="plan")
                child.append("cloned", {"parent_run_id": parent_run, "parent_sequence": parent_sequence, "parent_session": guard.relative(session_path), "replay": False}, mode="plan")
                payload = {"run_id": child_id, "path": guard.relative(child_path), "parent_run_id": parent_run, "parent_sequence": parent_sequence, "replay": False}
                if machine_json:
                    _emit_command_payload(args, command_name, "session_clone", payload, aliases=True, type_label="session_clone")
                else:
                    print(f"cloned run={child_id} parent={parent_run} sequence={parent_sequence} replay=false")
                return 0
            result = store.read_with_issues()
            last_state = _last_session_state(result.events)
            summary = {"id": session_path.stem, "path": guard.relative(session_path), "run_id": result.events[0].run_id if result.events else store.run_id, "events": len(result.events), "issues": [issue.__dict__ for issue in result.issues], "state": last_state or "unknown", "kinds": sorted({event.kind for event in result.events})}
            if machine_json:
                if summary["issues"]:
                    envelope = _machine_error(command_name, "session_issues", "session contains validation issues", exit_code=1, details={"issues": summary["issues"][:100]})
                    envelope.update(_compat_aliases(summary))
                    envelope["type"] = "session"
                    _emit_machine(envelope)
                else:
                    _emit_command_payload(args, command_name, "session", summary, aliases=True, type_label="session")
            else:
                print(f"session {summary['id']} run_id={summary['run_id']} events={summary['events']} state={summary['state']}")
                if summary["issues"]:
                    print("issues: " + "; ".join(f"line {issue['line']}: {issue['message']}" for issue in summary["issues"][:10]))
                print("kinds: " + ", ".join(summary["kinds"]))
            return 0 if not result.issues else 1
        except (OSError, ValueError, SessionFormatError) as exc:
            message = _redact_display(str(exc))
            if machine_json:
                envelope = _machine_error(command_name, "invalid_session", message, exit_code=2)
                envelope["type"] = "session"
                _emit_machine(envelope)
            else:
                print(f"invalid session: {message}", file=sys.stderr)
            return 2

    if command == "run":
        run_command_name = "run"
        if args.dry_run and not args.resume:
            message = "--dry-run requires --resume SESSION"
            if machine_json:
                _emit_machine(_machine_error(run_command_name, "invalid_arguments", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
        if args.force_recovery and not args.resume:
            message = "--force-recovery requires --resume SESSION"
            if machine_json:
                _emit_machine(_machine_error(run_command_name, "invalid_arguments", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
        if args.demo and args.resume:
            message = "--demo cannot be combined with --resume"
            if machine_json:
                _emit_machine(_machine_error(run_command_name, "invalid_arguments", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
        if args.verify and args.no_verify:
            message = "--verify cannot be combined with --no-verify"
            if machine_json:
                _emit_machine(_machine_error(run_command_name, "invalid_arguments", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
        if args.mode == AgentMode.PLAN.value and args.verify:
            message = "--verify is unavailable in plan mode; omit it or switch to act"
            if machine_json:
                _emit_machine(_machine_error(run_command_name, "invalid_arguments", message, exit_code=2))
            else:
                print(message, file=sys.stderr)
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
                    message = "task prompt is required when stdin is unavailable"
                    if machine_json:
                        _emit_machine(_machine_error(run_command_name, "missing_prompt", message, exit_code=2))
                    else:
                        print(message, file=sys.stderr)
                    return 2
                except KeyboardInterrupt:
                    message = "task input cancelled"
                    if machine_json:
                        _emit_machine(_machine_error(run_command_name, "cancelled", message, exit_code=130))
                    else:
                        print(message, file=sys.stderr)
                    return 130
        api_key = os.getenv(settings.api_key_env or "FORGECODE_API_KEY", "")
        machine_json = bool(args.json or getattr(args, "jsonl", False))
        configured_approval = settings.effective.approval if settings.effective else "interactive"
        approval = DenyAllApproval() if configured_approval == "deny" and not (args.auto_approve or args.demo) else InteractiveApproval(auto_approve=args.auto_approve or args.demo or configured_approval == "auto", output_fn=_approval_output(machine_json), prompt_to_output=machine_json, secrets=[api_key])
        new_run_id = uuid.uuid4().hex
        try:
            if args.resume:
                session_path = _resolve_session_reference(guard, workspace, args.resume, must_exist=True)
            else:
                session_path = _resolve_session_reference(guard, workspace, args.session) if args.session else _new_session_path(guard, new_run_id)
        except (OSError, ValueError) as exc:
            message = f"invalid session path: {exc}"
            if machine_json:
                _emit_machine(_machine_error(run_command_name, "invalid_session", _redact_display(message, [api_key]), exit_code=2))
            else:
                print(message, file=sys.stderr)
            return 2
        session = SessionStore(session_path, secrets=[api_key], run_id=None if args.resume else new_run_id, mode=args.mode)
        if not args.resume:
            session.append("tool_policy", tool_policy_summary, mode=args.mode)
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
                message = f"resume checkpoint not found for {session_path.stem}"
                if machine_json:
                    _emit_machine(_machine_error(run_command_name, "checkpoint_not_found", message, exit_code=2))
                else:
                    print(message, file=sys.stderr)
                return 2
            except (OSError, ValueError) as exc:
                message = f"resume checkpoint invalid: {_redact_display(str(exc), [api_key])}"
                if machine_json:
                    _emit_machine(_machine_error(run_command_name, "checkpoint_invalid", message, exit_code=2))
                else:
                    print(message, file=sys.stderr)
                return 2
            if conflicts:
                # The detailed recovery explanation is diagnostic output.  It
                # belongs on stderr in both machine transports; stdout gets a
                # single structured result below when the branch returns.
                _print_recovery(conflicts, json_mode=machine_json)
                try:
                    session.append("recovery_conflict", {"state": "recovery_required", "conflicts": [conflict.__dict__ for conflict in conflicts]}, mode=args.mode, error_code="recovery_conflict")
                except SessionFormatError:
                    # A completed/terminal parent cannot accept a lifecycle
                    # transition.  Preserve the immutable audit stream and
                    # still return the documented recovery conflict result.
                    pass
                except (OSError, ValueError) as exc:
                    message = f"could not record recovery conflict: {_redact_display(str(exc), [api_key])}"
                    if machine_json:
                        _emit_machine(_machine_error(run_command_name, "audit_write_failed", message, exit_code=1))
                    else:
                        print(message, file=sys.stderr)
                    return 1
                if args.dry_run:
                    payload = {"run_id": checkpoint.run_id, "state": "recovery_required", "mode": checkpoint.mode, "pending_actions": list(checkpoint.pending_actions), "files": [item.path for item in checkpoint.files], "conflicts": [conflict.__dict__ for conflict in conflicts]}
                    if machine_json:
                        envelope = _machine_error(run_command_name, "recovery_conflict", "checkpoint conflicts require recovery", exit_code=3, details={"conflicts": [conflict.__dict__ for conflict in conflicts][:100]})
                        envelope.update(_compat_aliases(payload))
                        envelope["type"] = "recovery"
                        _emit_machine(envelope)
                    return 3
                if not args.force_recovery:
                    try:
                        session.append("state_transition", {"from": checkpoint.state, "to": "recovery_required", "reason": "checkpoint conflict"}, mode=args.mode, error_code="recovery_conflict")
                    except (OSError, SessionFormatError, ValueError):
                        pass
                    if machine_json:
                        envelope = _machine_error(run_command_name, "recovery_conflict", "checkpoint conflicts require explicit recovery", exit_code=3, details={"conflicts": [conflict.__dict__ for conflict in conflicts][:100]})
                        envelope.update(_compat_aliases(payload if 'payload' in locals() else {"conflicts": [conflict.__dict__ for conflict in conflicts]}))
                        envelope["type"] = "recovery"
                        _emit_machine(envelope)
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
                if machine_json:
                    if conflicts:
                        envelope = _machine_error(run_command_name, "recovery_conflict", "checkpoint conflicts require explicit recovery", exit_code=3, details={"conflicts": [conflict.__dict__ for conflict in conflicts][:100]})
                        envelope.update(_compat_aliases(payload))
                    else:
                        envelope = _machine_envelope(run_command_name, "recovery_preview", True, data=payload, exit_code=0, **_compat_aliases(payload))
                    envelope["type"] = "recovery"
                    _emit_machine(envelope)
                else:
                    print(f"resume preview: run_id={checkpoint.run_id} state={checkpoint.state} files={len(checkpoint.files)} conflicts={len(conflicts)}")
                    if checkpoint.pending_actions:
                        print(f"pending actions require fresh approval: {len(checkpoint.pending_actions)}")
                return 3 if conflicts else 0
            if checkpoint.state == RunState.COMPLETED.value and not args.fork:
                payload = {"run_id": checkpoint.run_id, "state": checkpoint.state, "inspect_only": True, "message": "completed sessions are inspect-only; use --fork to start a new run"}
                if machine_json:
                    envelope = _machine_error(run_command_name, "completed_session", payload["message"], exit_code=3, details={"run_id": payload["run_id"], "state": payload["state"]})
                    envelope.update(_compat_aliases(payload))
                    envelope["type"] = "recovery"
                    _emit_machine(envelope)
                else:
                    print("completed session is inspect-only; use --fork to continue", file=sys.stderr)
                return 3
            if args.fork:
                # Never append follow-up events to the completed parent.  A
                # fork gets a fresh run/session identity and a durable link.
                session_path = _new_session_path(guard, new_run_id)
                session = SessionStore(session_path, secrets=[api_key], run_id=new_run_id, mode=args.mode)
                session.append("forked", {"parent_run_id": parent_run_id, "parent_sequence": checkpoint.sequence, "parent_session": guard.relative(parent_session_path), "state": checkpoint.state}, mode=args.mode)
            session.append("tool_policy", tool_policy_summary, mode=args.mode)
            rebuilt = SessionContextRebuilder().rebuild(SessionStore(parent_session_path), checkpoint)
            if rebuilt.conflicts:
                message = "resume context is inconsistent: " + "; ".join(rebuilt.conflicts[:10])
                if machine_json:
                    _emit_machine(_machine_error(run_command_name, "recovery_conflict", message, exit_code=3, details={"conflicts": list(rebuilt.conflicts)[:100]}))
                else:
                    print(message, file=sys.stderr)
                return 3
            recovered_summary = ContextCompactor(max_chars=12_000).compact_events(parent_events, checkpoint=checkpoint).summary
            prompt = _build_recovery_prompt(
                f"Parent run={rebuilt.run_id} state={rebuilt.state} sequence={rebuilt.sequence} "
                f"pending={len(rebuilt.pending_actions)} fingerprint={rebuilt.fingerprint}.\n"
                + recovered_summary,
                prompt,
            )
        if args.demo and args.mode == AgentMode.ACT.value:
            try:
                _prepare_demo_workspace(registry, guard, task=args.demo_task)
            except OSError as exc:
                message = _redact_display(str(exc), [api_key])
                if machine_json:
                    _emit_machine(_machine_error(run_command_name, "demo_setup_failed", message, exit_code=1))
                else:
                    print(f"forgecode run failed: {message}", file=sys.stderr)
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
                if machine_json:
                    envelope = _machine_error(run_command_name, "fatal_rule_diagnostics", "project rules contain fatal diagnostics", exit_code=2, details={"diagnostics": [item.to_dict() for item in rule_set.diagnostics if item.severity == "error"][:100]})
                    envelope["type"] = "error"
                    _emit_machine(envelope)
                else:
                    print("forgecode run failed: project rules contain fatal diagnostics", file=sys.stderr)
                return 2
            if reference_set.has_errors:
                session.append("context_source_error", {"error": "fatal reference diagnostics", "diagnostics": [item.to_dict() for item in reference_set.diagnostics if item.severity == "error"]}, mode=args.mode, error_code="context_source_error")
                if machine_json:
                    envelope = _machine_error(run_command_name, "fatal_reference_diagnostics", "explicit context references contain fatal diagnostics", exit_code=2, details={"diagnostics": [item.to_dict() for item in reference_set.diagnostics if item.severity == "error"][:100]})
                    envelope["type"] = "error"
                    _emit_machine(envelope)
                else:
                    print("forgecode run failed: explicit context references contain fatal diagnostics", file=sys.stderr)
                return 2
            session.append("rules_discovered", rule_set.to_dict(), mode=args.mode)
            session.append("references_resolved", {"items": [item.to_dict() for item in reference_set.items], "diagnostics": [item.to_dict() for item in reference_set.diagnostics], "fingerprint": reference_set.fingerprint}, mode=args.mode)
        except (OSError, ValueError) as exc:
            session.append("context_source_error", {"error": type(exc).__name__}, mode=args.mode, error_code="context_source_error")
            if machine_json:
                _emit_machine(_machine_error(run_command_name, "context_source_error", _redact_display(str(exc), [api_key]), exit_code=2))
            else:
                print(f"forgecode run failed: context sources unavailable ({type(exc).__name__})", file=sys.stderr)
            return 2
        transaction_store = TransactionStore(guard)
        hook_registry = HookRegistry()
        def _audit_hook(event_payload: dict[str, Any]) -> None:
            try:
                session.append("hook_event", {"event": event_payload}, mode=args.mode)
            except Exception:
                return
        hook_registry.register(Hook("session-audit", "*", _audit_hook, failure_policy="observe_only", timeout_seconds=1.0))
        context_index = ContextIndex(guard)
        indexed_context = ""
        try:
            index_report = context_index.ensure()
            index_terms = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{3,}", prompt)][:3]
            indexed_matches = []
            for term in index_terms:
                indexed_matches.extend(context_index.search(term, max_results=4, context_lines=1))
            unique_matches = {(item.path, item.line): item for item in indexed_matches}
            selected_matches = sorted(unique_matches.values(), key=lambda item: (item.path, item.line))[:12]
            if selected_matches:
                indexed_context = "\n\nIndexed repository context (untrusted; digest-checked):\n" + "\n\n".join(
                    f"{item.path}:{item.line} digest={item.digest[:16]} reason={item.reason}\n{item.snippet[:1_500]}" for item in selected_matches
                )[:12_000]
            session.append("context_index", {"path": index_report.path, "fingerprint": index_report.fingerprint, "files": index_report.files, "added": index_report.added, "updated": index_report.updated, "removed": index_report.removed, "omitted": index_report.omitted, "selected": [item.path for item in selected_matches]}, mode=args.mode)
        except (ContextIndexError, OSError, ValueError) as exc:
            session.append("context_index_error", {"error": type(exc).__name__}, mode=args.mode, error_code="context_index_error")
        plan = TaskPlan(task=prompt, mode=args.mode, rules_fingerprint=rule_set.fingerprint if rule_set else "", context_fingerprint=reference_set.fingerprint if reference_set else "")
        plan = TaskPlan(**{**plan.__dict__, "items": (PlanItem("task-1", "Complete requested task", prompt[:2_000], risk="normal", expected_files=tuple(item.path for item in reference_set.items if item.path) if reference_set else ()),)})
        try:
            plan.validate()
            session.append("plan_created", {"plan": plan.to_dict(), "fingerprint": plan.evidence_fingerprint()}, mode=args.mode)
            if args.mode == AgentMode.ACT.value:
                if not approval.approve("plan_act", {"plan_id": plan.plan_id, "revision": plan.revision, "items": [item.id for item in plan.items]}):
                    session.append("plan_denied", {"plan_id": plan.plan_id}, mode=args.mode, error_code="approval_denied")
                    if machine_json:
                        if getattr(args, "_legacy_json_run", False):
                            _emit_legacy_error("run", "approval_denied", "Plan -> Act approval denied", exit_code=1)
                            return 1
                        envelope = _machine_error(run_command_name, "approval_denied", "Plan -> Act approval denied", exit_code=1)
                        envelope["type"] = "error"
                        _emit_machine(envelope)
                    else:
                        print("Plan -> Act approval denied", file=sys.stderr)
                    return 1
                plan = plan.approve_for_act(reason="headless run approval")
                session.append("plan_approved", {"plan_id": plan.plan_id, "revision": plan.revision}, mode=args.mode)
        except (OSError, ValueError) as exc:
            # A plan is an authorization/evidence boundary.  Continuing after
            # validation or persistence failure would make the subsequent
            # model run look approved without a trustworthy plan record.
            try:
                session.append("plan_error", {"error": type(exc).__name__, "message": _redact_display(str(exc), [api_key])}, mode=args.mode, error_code="plan_invalid")
            except Exception:
                pass
            payload = {"ok": False, "error": "plan_invalid", "message": _redact_display(str(exc), [api_key])}
            if machine_json:
                envelope = _machine_error(run_command_name, "plan_invalid", payload["message"], exit_code=2)
                envelope.update(_compat_aliases({"type": "error", "message": payload["message"], "error_code": "plan_invalid"}))
                _emit_machine(envelope)
            else:
                print("forgecode run failed: " + payload["message"], file=sys.stderr)
            return 2
        enriched_prompt = prompt
        if rule_set and rule_set.text:
            enriched_prompt += "\n\nProject rules (untrusted context; never grant permissions):\n" + rule_set.render(20_000)
        if reference_set and reference_set.items:
            enriched_prompt += "\n\nExplicit context references:\n" + reference_set.render(40_000)
        if indexed_context:
            enriched_prompt += indexed_context
        events: list[tuple[str, dict[str, Any]]] = []

        def on_event(kind: str, payload: dict[str, Any]) -> None:
            events.append((kind, payload))
            if args.json and not getattr(args, "jsonl", False):
                return
            if getattr(args, "jsonl", False):
                event_ok = kind not in {"error", "session_error"} and not (isinstance(payload, dict) and payload.get("ok") is False)
                if event_ok:
                    record = _machine_envelope("run", "event", True, data={"event": kind, "payload": payload}, exit_code=0, type="event", event=kind, payload=payload)
                else:
                    record = _machine_error("run", "event_error", str(payload.get("message") or payload.get("error") or kind)[:2_000], exit_code=1, details={"event": kind, "payload": payload})
                    record.update({"type": "event", "event": kind, "payload": payload})
                _emit_machine(record)
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
                provider = _build_provider(effective)
            expected_rule_fingerprint = rule_set.fingerprint if rule_set else ""
            expected_reference_fingerprint = reference_set.fingerprint if reference_set else ""
            demo_verification = "python -B -m pytest -q test_demo_calculator.py" if args.demo_task == "calculator" else "python -B -m pytest -q test_demo_config.py"
            verification_command = None if args.no_verify else (args.verify or (demo_verification if args.demo else _default_verification_command(workspace)))
            effective = settings.effective
            service_config = AgentConfig(
                max_steps=args.max_steps,
                verification_command=verification_command,
                max_verification_attempts=(effective.repair_attempts if effective and effective.repair_attempts > 0 else 1),
                total_timeout_seconds=effective.run_timeout_seconds if effective else 600.0,
                provider_timeout_seconds=effective.provider_timeout_seconds if effective else 90.0,
                max_tool_calls_total=effective.max_tool_calls if effective else 512,
                compact_threshold_chars=effective.compact_threshold_chars if effective else None,
            )
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
                # The index is a cache and is expected to change after the
                # agent itself writes a planned target.  Snippets are
                # digest-checked at selection time; do not turn that normal
                # post-plan invalidation into a false optimistic-concurrency
                # conflict for the write operation.
                return True

            service = RunService(provider, registry, guard, session, service_config, settings.effective, approval, transaction_store, plan.plan_id, "task-1", expected_rule_fingerprint, plan.evidence_fingerprint(), expected_config_fingerprint, revalidate_context, hook_registry)
            result = asyncio.run(service.execute(enriched_prompt, mode=args.mode, secrets=tuple(secret for secret in (api_key,) if secret), on_event=on_event))
            try:
                updated_plan = plan.update_status("task-1", "in_progress", evidence={"run_id": result.run_id})
                updated_plan = updated_plan.update_status("task-1", "completed" if result.succeeded and result.verification_ok is not False else "failed", evidence={"stopped_reason": result.stopped_reason, "verification_ok": result.verification_ok, "audit_complete": result.audit_complete})
                session.append("plan_updated", {"plan": updated_plan.to_dict()}, mode=args.mode)
            except (OSError, ValueError):
                pass
        except (ProviderError, ValueError, OSError) as exc:
            error_code = getattr(exc, "category", "run_failed")
            error_message = _redact_display(str(exc), [api_key])
            if machine_json:
                if getattr(args, "_legacy_json_run", False):
                    _emit_legacy_error("run", "run_failed", error_message, exit_code=1, category=str(error_code)[:128], error_code="run_failed")
                    return 1
                envelope = _machine_error(run_command_name, "run_failed", error_message, exit_code=1, details={"category": str(error_code)[:128]})
                envelope.update(_compat_aliases({"type": "error", "error_code": "run_failed", "category": error_code, "message": error_message}))
                _emit_machine(envelope)
            else:
                print(f"forgecode run failed: {error_message}", file=sys.stderr)
            return 1

        if not machine_json:
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
        if machine_json:
            result_payload = {"stopped_reason": result.stopped_reason, "state": result.state, "run_id": result.run_id, "verification_ok": result.verification_ok, "succeeded": result.succeeded, "audit_complete": result.audit_complete}
            result_ok = bool(result.succeeded and result.verification_ok is not False and result.audit_complete)
            if result_ok:
                record = _machine_envelope(run_command_name, "result", True, data=result_payload, exit_code=0, type="result", **_compat_aliases(result_payload))
            else:
                record = _machine_error(run_command_name, "run_failed", "run did not complete successfully", exit_code=1, details=result_payload)
                record.update(_compat_aliases({"type": "result", **result_payload}))
            _emit_machine(record)
        if not result.audit_complete:
            print("[final] session audit incomplete", file=sys.stderr)
        return 0 if result.succeeded and result.verification_ok is not False and result.audit_complete else 1

    raise AssertionError(f"unhandled command: {command}")


def _resolve_session_reference(guard: WorkspaceGuard, workspace: Path, reference: Path, *, must_exist: bool = False) -> Path:
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
    resolved = guard.resolve(candidate, must_exist=must_exist)
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
