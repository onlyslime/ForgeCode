"""Stable machine-output envelopes for v0.0.8 command surfaces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgecode.cli import main
from forgecode.rpc import serve_lines


def test_rpc_describe_exposes_versioned_session_capabilities() -> None:
    rows = list(serve_lines([json.dumps({"id": "describe-1", "method": "rpc.describe", "params": {}})]))
    payload = json.loads(rows[0])
    assert payload["kind"] == "capabilities" and payload["data"]["version"] == 1
    assert "session.cancel" in payload["data"]["methods"]
    assert payload["data"]["safety"]["no_automatic_replay"] is True
    tools = payload["data"]["tools"]
    assert any(item["name"] == "git_worktree_create" and item["side_effecting"] for item in tools)
    assert payload["data"]["tool_capabilities_scope"].startswith("built_in_catalog")


def test_rpc_describe_honors_request_id_replay_contract() -> None:
    request = json.dumps({"id": "describe-replay", "method": "rpc.describe", "params": {}})
    first = list(serve_lines([request]))
    second = list(serve_lines([request]))
    assert first == second and len(second) == 1
from forgecode.application.commands import _build_recovery_prompt, _machine_envelope


def _json_lines(value: str) -> list[dict]:
    lines = [line for line in value.splitlines() if line.strip()]
    assert lines, value
    records = [json.loads(line) for line in lines]
    assert all(isinstance(record, dict) for record in records)
    return records


def _assert_envelope(record: dict, *, command: str, ok: bool) -> None:
    assert record["schema_version"] == 1
    assert isinstance(record["kind"], str) and record["kind"]
    assert record["command"] == command
    assert record["ok"] is ok
    assert ("data" in record) ^ ("error" in record)
    if not ok:
        assert isinstance(record["error"], dict)
        assert isinstance(record["error"].get("code"), str)
        assert isinstance(record["error"].get("message"), str)


def test_skills_json_and_jsonl_are_single_envelopes(capsys, tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "hello.md").write_text(
        "---\nid: hello\nversion: 1.0.0\nname: Hello\ndescription: hello\n---\nhello\n",
        encoding="utf-8",
    )

    assert main(["--workspace", str(tmp_path), "skills", "list", "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    records = _json_lines(output.out)
    assert len(records) == 1
    _assert_envelope(records[0], command="skills list", ok=True)
    assert records[0]["data"]["skills"][0]["manifest"]["id"] == "hello"

    # The global spelling is accepted before the subcommand as well.
    assert main(["--workspace", str(tmp_path), "--jsonl", "skills", "list"]) == 0
    records = _json_lines(capsys.readouterr().out)
    assert len(records) == 1
    _assert_envelope(records[0], command="skills list", ok=True)


def test_skill_input_is_bounded_before_json_decode(capsys, tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "echo.md").write_text(
        "---\nid: echo\nversion: 1.0.0\nname: Echo\ndescription: echo\n---\necho\n",
        encoding="utf-8",
    )
    oversized = '{"value":"' + ("x" * 256_001) + '"}'
    assert main(["--workspace", str(tmp_path), "skills", "run", "echo", "--input", oversized, "--jsonl"]) == 2
    payload = json.loads(capsys.readouterr().out)
    _assert_envelope(payload, command="skills run", ok=False)
    assert "safety limit" in payload["error"]["message"]


def test_recovery_prompt_stays_within_plan_task_bound_and_keeps_follow_up():
    prompt = _build_recovery_prompt("evidence " * 20_000, "continue with the requested follow-up")
    assert len(prompt) <= 8_000
    assert "Current follow-up:" in prompt
    assert "continue with the requested follow-up" in prompt


def test_context_jsonl_contains_results_in_one_envelope(capsys, tmp_path: Path):
    (tmp_path / "main.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "context", "search", "hello", "--jsonl"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    records = _json_lines(output.out)
    assert len(records) == 1
    _assert_envelope(records[0], command="context search", ok=True)
    assert records[0]["type"] == "context_summary"  # additive legacy label
    assert records[0]["data"]["results"]


def test_tools_json_exposes_stable_capability_categories(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "tools", "--jsonl"]) == 0
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command="tools", ok=True)
    categories = {row["category"] for row in record["data"]["tools"]}
    assert {"read_only", "changes", "execution", "evidence"} <= categories


def test_sessions_state_filter_is_bounded_and_machine_readable(capsys, tmp_path: Path):
    from forgecode.storage.session import SessionStore

    store = SessionStore(tmp_path / ".forgecode" / "sessions" / "done.jsonl", run_id="done")
    store.append("run_finished", {"state": "completed"}, outcome="completed")
    assert main(["--workspace", str(tmp_path), "sessions", "--state", "completed", "--jsonl"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["count"] == 1
    assert payload["data"]["sessions"][0]["state"] == "completed"


def test_machine_errors_use_error_object_and_keep_stdout_parseable(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "skills", "show", "missing", "--json"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    records = _json_lines(output.out)
    assert len(records) == 1
    _assert_envelope(records[0], command="skills show", ok=False)
    assert records[0]["error"]["code"]


def test_smoke_commands_emit_envelopes_and_keep_diagnostics_off_stdout(capsys, tmp_path: Path):
    cases = [
        (["doctor", "--json"], "doctor"),
        (["tools", "--jsonl"], "tools"),
        (["rules", "check", "--json"], "rules check"),
        (["config", "validate", "--jsonl"], "config validate"),
        (["provider", "health", "--json"], "provider health"),
    ]
    for argv, command in cases:
        assert main(["--workspace", str(tmp_path), *argv]) == 0
        output = capsys.readouterr()
        assert output.err == "", (command, output.err)
        records = _json_lines(output.out)
        assert len(records) == 1, (command, output.out)
        _assert_envelope(records[0], command=command, ok=True)
        assert isinstance(records[0]["data"], dict)


def test_doctor_configured_requires_model_and_selected_api_key(capsys, monkeypatch, tmp_path: Path):
    config_dir = tmp_path / ".forgecode"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        'model = "configured-model"\napi_key_env = "CUSTOM_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("CUSTOM_KEY", raising=False)
    assert main(["--workspace", str(tmp_path), "doctor", "--jsonl"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["configured"] is False
    assert payload["data"]["provider_health"]["configured"] is False
    monkeypatch.setenv("CUSTOM_KEY", "test-secret")
    assert main(["--workspace", str(tmp_path), "doctor", "--jsonl"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["configured"] is True
    assert payload["data"]["provider_health"]["configured"] is True
    assert "test-secret" not in json.dumps(payload)


def test_global_jsonl_flag_is_preserved_before_nested_commands(capsys, tmp_path: Path):
    for argv, command in [
        (["doctor"], "doctor"),
        (["rules", "check"], "rules check"),
        (["config", "validate"], "config validate"),
        (["provider", "health"], "provider health"),
    ]:
        assert main(["--workspace", str(tmp_path), "--jsonl", *argv]) == 0
        output = capsys.readouterr()
        assert output.err == ""
        record = _json_lines(output.out)[0]
        _assert_envelope(record, command=command, ok=True)


def test_run_jsonl_every_record_is_an_envelope_and_stdout_has_no_diagnostics(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--max-steps", "8", "--jsonl"]) == 0
    output = capsys.readouterr()
    assert output.err == "", output.err
    records = _json_lines(output.out)
    assert len(records) > 1  # progress events plus a terminal result
    for record in records:
        assert record["schema_version"] == 1
        assert record["command"] == "run"
        assert isinstance(record["kind"], str)
        assert isinstance(record["ok"], bool)
        assert ("data" in record) ^ ("error" in record)
    assert records[-1]["kind"] == "result" and records[-1]["data"]["succeeded"] is True


def test_chat_jsonl_header_and_results_are_envelopes(capsys, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("sys.stdin", iter(["/status", "/quit"]))
    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--auto-approve", "--jsonl"]) == 0
    output = capsys.readouterr()
    assert output.err == "", output.err
    records = _json_lines(output.out)
    assert records[0]["command"] == "chat" and records[0]["kind"] == "interactive_header"
    for record in records:
        assert record["schema_version"] == 1
        assert record["command"] == "chat"
        assert ("data" in record) ^ ("error" in record)


def test_machine_envelope_does_not_allow_compatibility_aliases_to_shadow_schema():
    success = _machine_envelope(
        "demo",
        "result",
        True,
        data={"value": 1},
        schema_version=99,
    )
    assert success["schema_version"] == 1
    assert success["kind"] == "result"
    assert success["ok"] is True
    assert success["command"] == "demo"
    assert success["data"] == {"value": 1}
    assert "error" not in success

    failure = _machine_envelope(
        "demo",
        "error",
        False,
        error={"code": "bad", "message": "bad"},
        schema_version=99,
    )
    assert failure["schema_version"] == 1
    assert failure["ok"] is False
    assert "data" not in failure
    assert failure["error"]["code"] == "bad"


def test_json_and_jsonl_are_mutually_exclusive_structured_error(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "doctor", "--json", "--jsonl"]) == 2
    output = capsys.readouterr()
    record = _json_lines(output.out)[0]
    _assert_envelope(record, command="doctor", ok=False)
    assert record["error"]["code"] == "conflicting_output_modes"
    assert output.err == ""

    # Parent/leaf ordering must be checked as well.
    assert main(["--workspace", str(tmp_path), "test", "--json", "list", "--jsonl"]) == 2
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command="test list", ok=False)
    assert record["error"]["code"] == "conflicting_output_modes"


def test_parse_errors_with_machine_flag_return_an_envelope(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "doctor", "--unknown", "--jsonl"]) == 2
    output = capsys.readouterr()
    record = _json_lines(output.out)[0]
    _assert_envelope(record, command="doctor", ok=False)
    assert record["error"]["code"] == "invalid_arguments"


def test_failed_test_run_uses_error_envelope_and_exit_code(capsys, tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    (directory / "tests.toml").write_text(
        "[profiles.default]\ncommand = [\"python\", \"-c\", \"raise SystemExit(4)\"]\n",
        encoding="utf-8",
    )
    assert main(["--workspace", str(tmp_path), "test", "run", "default", "--auto-approve", "--jsonl"]) == 1
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command="test run", ok=False)
    assert record["error"]["code"] == "test_failed"
    assert record["error"]["details"]["evidence"]["exit_code"] == 4
    assert "data" not in record


def test_review_nonzero_report_uses_error_envelope(capsys, tmp_path: Path):
    # A malformed session produces a deterministic recovery conflict without
    # requiring a provider or a transaction.
    sessions = tmp_path / ".forgecode" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "broken.jsonl").write_text('{"schema_version": 1, "broken": true}\n', encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "review", "--session", "broken", "--jsonl"]) == 3
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command="review", ok=False)
    assert record["error"]["code"] == "review_conflict"
    assert record["error"]["details"]["report"]["exit_code"] == 3
    assert "data" not in record


def test_transaction_conflict_jsonl_returns_conflict_exit_code(capsys, tmp_path: Path):
    # There is no ledger yet; the command must still emit one parseable error
    # envelope and preserve its non-success exit code.
    assert main(["--workspace", str(tmp_path), "transaction", "missing", "--jsonl"]) == 2
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command="transaction", ok=False)
    assert record["error"]["code"] in {"transaction_unavailable", "transaction_failed"}
    assert record["exit_code"] == 2


def test_session_issue_json_and_jsonl_have_error_not_data(capsys, tmp_path: Path):
    sessions = tmp_path / ".forgecode" / "sessions"
    sessions.mkdir(parents=True)
    path = sessions / "broken.jsonl"
    path.write_text('{"schema_version": 1, "broken": true}\n', encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "session", "show", "broken", "--jsonl"]) == 1
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command="session show", ok=False)
    assert record["error"]["code"] == "session_issues"
    assert "data" not in record


@pytest.mark.parametrize(
    "argv, command",
    [
        (["review", "--session", "missing", "--jsonl"], "review"),
        (["diff", "--session", "missing", "--jsonl"], "diff"),
        (["status", "--session", "missing", "--jsonl"], "status"),
        (["session", "show", "missing", "--jsonl"], "session show"),
        (["session", "inspect", "missing", "--jsonl"], "session inspect"),
    ],
)
def test_read_only_session_commands_reject_missing_references(capsys, tmp_path: Path, argv: list[str], command: str):
    """Inspection must not silently construct an empty session for a typo."""
    assert main(["--workspace", str(tmp_path), *argv]) in {1, 2, 3}
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command=command, ok=False)
    assert record["error"]["code"] in {"invalid_session", "session_unavailable"}


def test_run_failure_json_and_jsonl_keep_compatibility_aliases(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "offline request", "--mode", "act", "--auto-approve", "--json"]) == 1
    record = _json_lines(capsys.readouterr().out)[0]
    _assert_envelope(record, command="run", ok=False)
    assert record["error"]["code"] == "run_failed"
    assert record["ok"] is False
    assert "data" not in record
    assert record.get("type") == "error"
