from forgecode.cli import main


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
