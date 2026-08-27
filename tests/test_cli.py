from forgecode.cli import main


def test_doctor_command(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "doctor"]) == 0
    output = capsys.readouterr().out
    assert "status: ready" in output
    assert "read_file" in output


def test_demo_run_is_offline_and_reports_repair_and_verification(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve"]) == 0
    output = capsys.readouterr().out
    assert "intentional failure" in output
    assert "repair passed" in output
    assert "[verify] passed" in output
    assert "stop=model_finished" in output
    assert (tmp_path / ".forgecode" / "demo.txt").read_text(encoding="utf-8") == "ForgeCode demo\n"


def test_cli_rejects_session_path_outside_workspace(capsys, tmp_path):
    outside = tmp_path.parent / "outside-session.jsonl"
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--session", str(outside)]) == 2
    assert "invalid session path" in capsys.readouterr().err
