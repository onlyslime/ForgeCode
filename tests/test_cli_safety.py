import json
from pathlib import Path

from forgecode.cli import main


def test_cli_rejects_conflicting_flags(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--dry-run"]) == 2
    assert "requires --resume" in capsys.readouterr().err
    assert main(["--workspace", str(tmp_path), "run", "--mode", "plan", "--verify", "echo ok", "x"]) == 2
    assert "unavailable in plan" in capsys.readouterr().err
    assert main(["--workspace", str(tmp_path), "run", "--verify", "echo", "--no-verify", "x"]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_recovery_conflict_is_recorded_during_dry_run(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve"]) == 0
    capsys.readouterr()
    target = tmp_path / "demo_calculator.py"
    target.write_text(target.read_text(encoding="utf-8") + "# outside\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "run", "--resume", "latest", "--dry-run"]) == 3
    capsys.readouterr()
    events = list(json.loads(line) for line in next((tmp_path / ".forgecode" / "sessions").glob("*.jsonl")).read_text(encoding="utf-8").splitlines())
    assert any(event["kind"] == "recovery_conflict" for event in events)

    capsys.readouterr()
    assert main(["--workspace", str(tmp_path), "session", "show", "latest"]) == 0
    assert "state=recovery_required" in capsys.readouterr().out
