"""CLI contracts for the first-class evidence-driven review command."""

from __future__ import annotations

import json
from pathlib import Path

from forgecode.cli import main


def _one_json_line(output: str) -> dict:
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1, output
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    return value


def test_review_json_and_jsonl_are_single_stable_envelopes(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "review", "--json"]) == 0
    payload = _one_json_line(capsys.readouterr().out)
    assert payload["kind"] == "review_report"
    assert payload["ok"] is True and payload["exit_code"] == 0
    assert payload["report"]["workspace"] == "."
    assert payload["review"]["audit_complete"] is True

    assert main(["--workspace", str(tmp_path), "review", "--jsonl"]) == 0
    payload = _one_json_line(capsys.readouterr().out)
    assert payload["kind"] == "review_report"


def test_review_export_import_and_stale_verification(capsys, tmp_path: Path):
    # The offline demo uses the production plan/agent/tool/transaction path,
    # giving the review report real session and hunk evidence.
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--json"]) == 0
    capsys.readouterr()

    assert main(["--workspace", str(tmp_path), "review", "--export", "review.json", "--json"]) == 0
    exported = _one_json_line(capsys.readouterr().out)
    assert exported["artifact"] == {"action": "export", "path": "review.json", "verify_files": True}
    assert (tmp_path / "review.json").is_file()

    assert main(["--workspace", str(tmp_path), "review", "--verify", "review.json", "--jsonl"]) == 0
    verified = _one_json_line(capsys.readouterr().out)
    assert verified["artifact"]["action"] == "verify"
    report_id = verified["report"]["report_id"]

    # A current-file digest mismatch is a conflict (exit 3), never a pass.
    (tmp_path / "demo_calculator.py").write_text("external edit\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "review", "--import", "review.json", "--json"]) == 3
    stale = _one_json_line(capsys.readouterr().out)
    assert stale["kind"] == "error" and stale["error"]["code"] == "artifact_invalid"
    assert report_id  # keep the successful verification assertion explicit


def test_review_session_and_transaction_options_are_validated(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "review", "--transaction", "one", "two", "--json"]) == 2
    payload = _one_json_line(capsys.readouterr().out)
    assert payload["error"]["code"] == "conflicting_transaction"

    outside = tmp_path.parent / "outside-review-session.jsonl"
    assert main(["--workspace", str(tmp_path), "review", "--session", str(outside), "--json"]) == 2
    payload = _one_json_line(capsys.readouterr().out)
    assert payload["error"]["code"] == "invalid_session"
