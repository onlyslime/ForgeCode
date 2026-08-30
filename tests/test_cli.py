from forgecode.cli import _insert_chat_after_global_options, fc_main, main


def test_fcc_launcher_keeps_global_options_before_chat():
    assert _insert_chat_after_global_options(["--workspace", "work", "--json"]) == [
        "--workspace", "work", "--json", "chat"
    ]
    assert _insert_chat_after_global_options(["--jsonl", "--bypass"]) == [
        "--jsonl", "chat", "--bypass"
    ]
    assert _insert_chat_after_global_options(["--workspace=work", "--bypass"]) == [
        "--workspace=work", "chat", "--bypass"
    ]


def test_fcc_version_passthrough(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["fcc", "--version"])
    import pytest
    with pytest.raises(SystemExit) as exc:
        fc_main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip()


def test_doctor_command(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "doctor"]) == 0
    output = capsys.readouterr().out
    assert "status: ready" in output
    assert "read_file" in output


def test_demo_run_is_offline_and_reports_repair_and_verification(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve"]) == 0
    output = capsys.readouterr().out
    assert "FAILED" in output
    assert "apply_patch" in output
    assert "passed" in output
    assert "stop=model_finished" in output
    assert (tmp_path / "demo_calculator.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_demo_uses_fresh_source_after_same_size_atomic_patch(capsys, tmp_path):
    """The repair must be observed even when old/new source have equal length."""
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve"]) == 0
    output = capsys.readouterr().out
    assert "[verify] passed" in output
    assert "stop=model_finished verification=True" in output


def test_cli_rejects_session_path_outside_workspace(capsys, tmp_path):
    outside = tmp_path.parent / "outside-session.jsonl"
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--session", str(outside)]) == 2
    assert "invalid session path" in capsys.readouterr().err


def test_cli_help_describes_mode(capsys):
    try:
        main(["run", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "--mode" in output
    assert "plan" in output


def test_plan_demo_does_not_write_or_run_commands(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--mode", "plan", "--auto-approve"]) == 0
    output = capsys.readouterr().out
    assert "[mode] plan" in output
    assert "side effects disabled" in output
    assert "no files or commands were executed" in output
    assert not (tmp_path / "demo_calculator.py").exists()


def test_demo_rejects_existing_fixture_instead_of_overwriting(capsys, tmp_path):
    (tmp_path / "demo_calculator.py").write_text("user content", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve"]) == 1
    assert "already contains demo_calculator.py" in capsys.readouterr().err
    assert (tmp_path / "demo_calculator.py").read_text(encoding="utf-8") == "user content"


def test_inspect_and_sessions_commands_are_bounded(capsys, tmp_path):
    (tmp_path / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "inspect", "--task", "main"]) == 0
    assert "main.py" in capsys.readouterr().out
    assert main(["--workspace", str(tmp_path), "sessions"]) == 0
    assert "no sessions" in capsys.readouterr().out


def test_session_show_and_export_after_run(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--mode", "plan", "--auto-approve"]) == 0
    capsys.readouterr()
    assert main(["--workspace", str(tmp_path), "session", "show", "latest"]) == 0
    assert "state=completed" in capsys.readouterr().out
    assert main(["--workspace", str(tmp_path), "session", "export", "latest", "--max-chars", "2_000"]) == 0
    assert "schema_version" in capsys.readouterr().out


def test_json_demo_is_a_second_real_offline_task(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--demo-task", "json", "--auto-approve"]) == 0
    output = capsys.readouterr().out
    assert "test_demo_config.py" in output
    assert (tmp_path / "demo_config.json").read_text(encoding="utf-8").find('"enabled": true') >= 0


def test_run_json_emits_only_one_json_document(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--json"]) == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["succeeded"] is True and payload["state"] == "completed"


def test_global_json_flag_is_preserved_for_read_only_subcommands(capsys, tmp_path):
    (tmp_path / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    import json

    assert main(["--workspace", str(tmp_path), "--json", "inspect", "--task", "main"]) == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert "snapshot" in inspect_payload and "context" in inspect_payload

    assert main(["--workspace", str(tmp_path), "--json", "sessions"]) == 0
    sessions_payload = json.loads(capsys.readouterr().out)
    assert isinstance(sessions_payload, list)


def test_doctor_and_tools_json_work_before_or_after_subcommand(capsys, tmp_path):
    import json

    assert main(["--workspace", str(tmp_path), "--json", "doctor"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["status"] == "ready" and doctor["workspace"] == "."

    assert main(["--workspace", str(tmp_path), "tools", "--json"]) == 0
    tools = json.loads(capsys.readouterr().out)
    assert any(tool["name"] == "apply_patch" and tool["side_effecting"] for tool in tools)
