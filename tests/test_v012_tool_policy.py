"""Focused contracts for Pi-inspired runtime tool policies."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from forgecode.application import commands as command_module
from forgecode.config import ConfigError, ToolPolicy, parse_tool_policy_options
from forgecode.models import Message, ModelResponse
from forgecode.security.workspace import WorkspaceGuard
from forgecode.tools import AllowAllApproval, DenyAllApproval, RiskScopedApproval, ToolContext, build_default_registry


def test_risk_scoped_approval_overrides_groups_and_delegates() -> None:
    policy = RiskScopedApproval(DenyAllApproval(), {"changes": "allow", "execution": "deny"})
    assert policy.approve("write_file", {}) is True
    assert policy.approve("run_command", {}) is False
    assert policy.approve("git_status", {}) is False
    assert policy.last_decision == "fallback"
    assert policy.approve("write_file", {}) is True
    assert policy.last_decision == "scope_changes_allow"
    assert policy.approve("run_command", {}) is False
    assert policy.last_decision == "scope_execution_deny"


def test_risk_scoped_approval_rejects_invalid_decisions() -> None:
    with pytest.raises(ValueError):
        RiskScopedApproval(AllowAllApproval(), {"execution": "maybe"})


def test_risk_scoped_ask_delegates_without_silent_permission() -> None:
    policy = RiskScopedApproval(AllowAllApproval(), {"execution": "ask"})
    assert policy.approve("run_command", {}) is True
    assert policy.last_decision == "fallback"


def test_git_worktree_listing_is_read_only_and_bounded(tmp_path) -> None:
    from forgecode.tools import GitWorktreeListTool
    guard = WorkspaceGuard(tmp_path)
    result = GitWorktreeListTool(guard).execute({}, ToolContext(guard))
    assert result.ok is False or result.metadata.get("count", 0) <= 64


def _available() -> tuple[str, ...]:
    return build_default_registry(WorkspaceGuard(Path.cwd())).names()


def test_cli_tool_policy_parser_is_bounded_and_fail_closed() -> None:
    available = _available()
    policy = parse_tool_policy_options("read_file,search", available=available)
    assert policy == ToolPolicy(allow=("read_file", "search"))
    assert parse_tool_policy_options(None, None, available=available) is None
    assert parse_tool_policy_options(None, "run_command", available=available) == ToolPolicy(deny=("run_command",))
    assert parse_tool_policy_options(None, None, no_tools=True, available=available) == ToolPolicy(deny=available)
    with pytest.raises(ConfigError, match="unknown tools"):
        parse_tool_policy_options("missing_tool", available=available)
    with pytest.raises(ConfigError, match="duplicate"):
        parse_tool_policy_options("read_file,read_file", available=available)
    with pytest.raises(ConfigError, match="overlap"):
        parse_tool_policy_options("read_file", "read_file", available=available)
    with pytest.raises(ConfigError, match="cannot be combined"):
        parse_tool_policy_options("read_file", None, no_tools=True, available=available)


def test_cli_tool_policy_supports_audited_risk_groups() -> None:
    available = ("read_file", "search", "write_file", "run_command")
    assert parse_tool_policy_options("read_only", available=available) == ToolPolicy(allow=("read_file", "search"))
    assert parse_tool_policy_options(None, "execution", available=available) == ToolPolicy(deny=("run_command",))


def test_execution_group_includes_background_lifecycle_tools() -> None:
    available = ("run_background", "process_status", "poll_process", "list_processes", "kill_process")
    policy = parse_tool_policy_options("execution", available=available)
    assert set(policy.allow) == set(available)


def test_registry_policy_intersection_preserves_stable_unavailable_result(tmp_path: Path) -> None:
    guard = WorkspaceGuard(tmp_path)
    base = build_default_registry(guard)
    configured = base.filter(ToolPolicy(allow=("read_file", "search")))
    narrowed = configured.filter(ToolPolicy(allow=("search",)))
    assert narrowed.names() == ("search",)
    assert "run_command" in narrowed.unavailable_names()
    result = narrowed.execute("run_command", {"command": "echo should-not-run"}, ToolContext(guard))
    assert result.ok is False
    assert result.metadata["error"] == "tool_unavailable"


def test_chat_tools_allowlist_reaches_provider_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    requests: list[tuple[tuple[Message, ...], tuple[dict, ...]]] = []

    class Provider:
        def __init__(self, **_: object) -> None:
            pass

        async def complete(self, messages, tools, context=None):  # type: ignore[no-untyped-def]
            requests.append((tuple(messages), tuple(tools)))
            return ModelResponse(Message("assistant", "done"))

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", Provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("inspect\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--tools", "read_file,search", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert requests
    names = [item["function"]["name"] for item in requests[0][1]]
    assert names == ["read_file", "search"]
    assert all(record.get("kind") != "tool_policy_invalid" for record in records)


def test_cli_allowlist_cannot_expand_configured_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config_dir = tmp_path / ".forgecode"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[tool_policy]\nallow = ["read_file"]\n', encoding="utf-8")
    requests: list[tuple[tuple[Message, ...], tuple[dict, ...]]] = []

    class Provider:
        def __init__(self, **_: object) -> None:
            pass

        async def complete(self, messages, tools, context=None):  # type: ignore[no-untyped-def]
            requests.append((tuple(messages), tuple(tools)))
            return ModelResponse(Message("assistant", "bounded"))

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", Provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("hello\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--tools", "write_file", "--jsonl", "--auto-approve"]) == 0
    capsys.readouterr()
    assert requests and requests[0][1] == ()


def test_chat_no_tools_sends_empty_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    requests: list[tuple[tuple[Message, ...], tuple[dict, ...]]] = []

    class Provider:
        def __init__(self, **_: object) -> None:
            pass

        async def complete(self, messages, tools, context=None):  # type: ignore[no-untyped-def]
            requests.append((tuple(messages), tuple(tools)))
            return ModelResponse(Message("assistant", "natural language only"))

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", Provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("hello\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--no-tools", "--jsonl", "--auto-approve"]) == 0
    capsys.readouterr()
    assert requests and requests[0][1] == ()


def test_run_tools_allowlist_is_used_by_agent_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    requests: list[tuple[tuple[Message, ...], tuple[dict, ...]]] = []

    class Provider:
        def __init__(self, **_: object) -> None:
            pass

        async def complete(self, messages, tools, context=None):  # type: ignore[no-untyped-def]
            requests.append((tuple(messages), tuple(tools)))
            return ModelResponse(Message("assistant", "run policy complete"))

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", Provider)
    assert command_module.main(["--workspace", str(tmp_path), "run", "inspect", "--mode", "plan", "--tools", "read_file,search", "--no-verify", "--jsonl"]) == 0
    capsys.readouterr()
    assert requests
    assert [item["function"]["name"] for item in requests[0][1]] == ["read_file", "search"]


def test_disabled_shortcuts_are_structured_and_do_not_construct_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class ExplodingProvider:
        def __init__(self, **_: object) -> None:
            raise AssertionError("disabled shortcut must not construct a provider")

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", ExplodingProvider)
    monkeypatch.setattr("sys.stdin", io.StringIO("!! echo should-not-run\n! echo also-not-run\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--exclude-tools", "run_command", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    shortcut_results = [record for record in records if record.get("payload", {}).get("shortcut") in {"!", "!!"}]
    assert len(shortcut_results) == 2
    assert all(record["payload"]["code"] == "tool_unavailable" for record in shortcut_results)
    assert all(record["payload"]["ok"] is False for record in shortcut_results)


def test_configured_deny_also_blocks_shortcut_without_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config_dir = tmp_path / ".forgecode"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[tool_policy]\ndeny = ["run_command"]\n', encoding="utf-8")

    class ExplodingProvider:
        def __init__(self, **_: object) -> None:
            raise AssertionError("configured deny must block before provider construction")

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", ExplodingProvider)
    monkeypatch.setattr("sys.stdin", io.StringIO("!! echo should-not-run\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    result = next(record for record in records if record.get("payload", {}).get("shortcut") == "!!")
    assert result["payload"]["code"] == "tool_unavailable"
    session_path = next((tmp_path / ".forgecode" / "sessions").glob("*.jsonl"))
    assert '"kind":"tool_policy"' in session_path.read_text(encoding="utf-8")


def test_invalid_tool_policy_has_machine_error_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("ignored\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--tools", "not_a_tool", "--jsonl"]) == 2
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["ok"] is False
    assert records[0]["error"]["code"] == "tool_policy_invalid"
