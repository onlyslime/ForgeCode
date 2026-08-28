from __future__ import annotations

import json
from pathlib import Path

from forgecode.cli import main


def _write_profiles(workspace: Path, body: str) -> None:
    directory = workspace / ".forgecode"
    directory.mkdir()
    (directory / "tests.toml").write_text(body, encoding="utf-8")


def test_test_list_json_and_jsonl_are_single_bounded_envelopes(capsys, tmp_path: Path):
    _write_profiles(
        tmp_path,
        """
version = 1
default_profile = "quick"
[profiles.default]
command = ["python", "-m", "pytest", "-q"]
[profiles.quick]
command = ["python", "-c", "print(42)"]
description = "fast local check"
""",
    )

    assert main(["--workspace", str(tmp_path), "test", "list", "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    payload = json.loads(output.out)
    assert payload["schema_version"] == 1
    assert payload["kind"] == "test_profiles"
    assert payload["ok"] is True
    assert payload["data"]["default_profile"] == "quick"
    assert [item["name"] for item in payload["data"]["profiles"]] == ["default", "quick"]

    assert main(["--workspace", str(tmp_path), "test", "list", "--jsonl"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["command"] == "test list"


def test_test_show_json_and_profile_selection_from_environment(capsys, monkeypatch, tmp_path: Path):
    _write_profiles(
        tmp_path,
        """
[profiles.default]
command = ["python", "-c", "print('default')"]
[profiles.quick]
command = ["python", "-c", "print('quick')"]
""",
    )
    monkeypatch.setenv("FORGECODE_TEST_PROFILE", "quick")
    assert main(["--workspace", str(tmp_path), "test", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "test_profile"
    assert payload["data"]["profile"]["name"] == "quick"
    assert payload["data"]["selected_by"] == "environment"


def test_test_run_persists_evidence_and_keeps_machine_stdout_pure(capsys, tmp_path: Path):
    _write_profiles(
        tmp_path,
        """
[profiles.default]
command = ["python", "-c", "print('profile-ok')"]
""",
    )
    assert main(["--workspace", str(tmp_path), "test", "run", "default", "--auto-approve", "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    payload = json.loads(output.out)
    assert payload["kind"] == "test_profile_result"
    assert payload["ok"] is True
    evidence = payload["data"]["evidence"]
    assert evidence["profile"] == "default"
    assert evidence["verification_status"] == "passed"
    assert "profile-ok" in evidence["stdout_preview"]
    session_path = tmp_path / payload["data"]["session"]
    assert session_path.is_file()
    event = json.loads(session_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["kind"] == "test_profile_result"
    assert event["payload"]["evidence_id"] == evidence["evidence_id"]


def test_test_run_plan_and_approval_denial_are_never_success(capsys, tmp_path: Path):
    _write_profiles(
        tmp_path,
        """
[profiles.default]
command = ["python", "-c", "print('must-not-run')"]
""",
    )
    assert main(["--workspace", str(tmp_path), "test", "run", "default", "--mode", "plan", "--json"]) == 1
    planned = json.loads(capsys.readouterr().out)
    assert planned["ok"] is False
    assert "data" not in planned
    assert planned["error"]["details"]["evidence"]["verification_status"] == "skipped"

    assert main(["--workspace", str(tmp_path), "test", "run", "default", "--json"]) == 1
    denied = json.loads(capsys.readouterr().out)
    assert denied["ok"] is False
    assert "data" not in denied
    assert denied["error"]["code"] == "approval_denied"
    assert denied["error"]["details"]["evidence"]["approval"] == "denied"
    assert denied["error"]["details"]["evidence"]["error_code"] == "approval_denied"


def test_test_run_honors_explicit_profile_auto_approval_in_json(capsys, tmp_path: Path):
    _write_profiles(
        tmp_path,
        """
[profiles.default]
command = ["python", "-c", "print('auto')"]
approval = "auto"
""",
    )
    assert main(["--workspace", str(tmp_path), "test", "run", "default", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["evidence"]["approval"] == "approved"


def test_test_run_timeout_returns_nonzero_evidence(capsys, tmp_path: Path):
    _write_profiles(
        tmp_path,
        """
[profiles.default]
command = ["python", "-c", "import time; time.sleep(2)"]
timeout_seconds = 5
""",
    )
    assert main(["--workspace", str(tmp_path), "test", "run", "default", "--auto-approve", "--timeout", "0.1", "--jsonl"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is False
    assert "data" not in payload
    assert payload["error"]["details"]["evidence"]["timed_out"] is True
    assert payload["error"]["details"]["evidence"]["verification_status"] == "timed_out"


def test_test_invalid_profile_config_is_structured_and_does_not_echo_secret(capsys, tmp_path: Path):
    _write_profiles(tmp_path, '[profiles.default]\ncommand = "python -c print(1)"\n')
    assert main(["--workspace", str(tmp_path), "test", "list", "--json"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    payload = json.loads(output.out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_profile_config"
    assert "python -c" not in output.out
