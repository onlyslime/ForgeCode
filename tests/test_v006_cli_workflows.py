import hashlib
import json
from pathlib import Path

from forgecode.cli import main


def _latest_session(workspace: Path) -> Path:
    return max((workspace / ".forgecode" / "sessions").glob("*.jsonl"), key=lambda path: path.stat().st_mtime_ns)


def test_rules_config_plan_cli_json_are_stable(capsys, tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Run tests.\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "rules", "show", "a.py", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sources"][0]["path"] == "AGENTS.md"
    assert main(["--workspace", str(tmp_path), "config", "validate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    before = (tmp_path / "a.py").read_bytes()
    assert main(["--workspace", str(tmp_path), "plan", "inspect", "@a.py", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["items"][0]["expected_files"] == ["a.py"]
    assert (tmp_path / "a.py").read_bytes() == before


def test_cli_transaction_undo_conflict_and_repeated_undo(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--json"]) == 0
    capsys.readouterr()
    fixed = (tmp_path / "demo_calculator.py").read_bytes()
    assert main(["--workspace", str(tmp_path), "transaction", "--json"]) == 0
    transaction = json.loads(capsys.readouterr().out)
    assert transaction["rollback_available"] is True and transaction["verification"]["ok"] is True
    (tmp_path / "demo_calculator.py").write_text("external\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "transaction", transaction["transaction_id"], "--execute", "--auto-approve", "--json"]) == 3
    assert (tmp_path / "demo_calculator.py").read_text(encoding="utf-8") == "external\n"

    # Restore the exact expected after bytes to demonstrate a valid undo.
    (tmp_path / "demo_calculator.py").write_bytes(fixed)
    assert main(["--workspace", str(tmp_path), "transaction", transaction["transaction_id"], "--execute", "--auto-approve", "--json"]) == 0
    capsys.readouterr()
    assert b"return a - b" in (tmp_path / "demo_calculator.py").read_bytes()
    assert main(["--workspace", str(tmp_path), "transaction", transaction["transaction_id"], "--execute", "--auto-approve", "--json"]) in {2, 3}
    assert b"return a - b" in (tmp_path / "demo_calculator.py").read_bytes()


def test_completed_resume_is_inspect_only_and_session_fork_has_parent(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--json"]) == 0
    run = json.loads(capsys.readouterr().out)
    assert main(["--workspace", str(tmp_path), "run", "--resume", "latest", "follow", "--json"]) == 3
    refused = json.loads(capsys.readouterr().out)
    assert refused["inspect_only"] is True
    assert main(["--workspace", str(tmp_path), "session", "fork", "latest", "--json"]) == 0
    forked = json.loads(capsys.readouterr().out)
    assert forked["parent_run_id"] == run["run_id"] and forked["run_id"] != run["run_id"]
    child_path = tmp_path / forked["path"]
    child_event = json.loads(child_path.read_text(encoding="utf-8").splitlines()[0])
    assert child_event["kind"] == "forked" and child_event["payload"]["parent_sequence"] > 0


def test_session_compact_cli_keeps_original_prefix_and_inspect_rebuilds(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--mode", "plan", "--auto-approve", "--json"]) == 0
    capsys.readouterr()
    path = _latest_session(tmp_path)
    before = path.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()
    assert main(["--workspace", str(tmp_path), "session", "compact", "latest", "--max-chars", "4000", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)
    after = path.read_bytes()
    assert after.startswith(before) and compact["source_sequence_end"] > 0
    assert hashlib.sha256(after[: len(before)]).hexdigest() == before_hash
    assert main(["--workspace", str(tmp_path), "session", "inspect", "latest", "--json"]) == 0
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["run_id"] and rebuilt["sequence"] > compact["source_sequence_end"]


def test_scripted_interactive_jsonl_plan_act_review_compact_undo(capsys, monkeypatch, tmp_path: Path):
    lines = iter(["inspect calculator", "/mode act", "fix calculator", "/review", "/compact", "/undo latest", "/quit"])
    monkeypatch.setattr("sys.stdin", lines)
    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--auto-approve", "--json"]) == 0
    output = capsys.readouterr().out.splitlines()
    events = [json.loads(line) for line in output]
    assert events[0]["type"] == "interactive_header"
    payloads = [event["payload"] for event in events[1:] if event.get("type") == "interactive_result"]
    assert any(payload.get("mode") == "act" for payload in payloads if isinstance(payload, dict))
    assert any(payload.get("omitted_events") is not None for payload in payloads if isinstance(payload, dict))
    assert any(payload.get("parent_transaction_id") for payload in payloads if isinstance(payload, dict))
    assert b"return a - b" in (tmp_path / "demo_calculator.py").read_bytes()


def test_config_invalid_plaintext_secret_fails_closed(capsys, tmp_path: Path):
    config = tmp_path / ".forgecode" / "config.toml"
    config.parent.mkdir()
    config.write_text('api_key = "do-not-store"\n', encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "config", "validate", "--json"]) == 2
    output = capsys.readouterr()
    assert "do-not-store" not in output.out + output.err


def test_chat_existing_session_refuses_mixed_run_append(capsys, tmp_path: Path):
    from forgecode.storage import SessionStore

    path = tmp_path / ".forgecode" / "sessions" / "existing.jsonl"
    store = SessionStore(path, run_id="original-run", mode="plan")
    store.append("user_message", {"content": "original"})
    before = path.read_bytes()

    assert main(["--workspace", str(tmp_path), "chat", "--session", str(path), "--json"]) == 3
    output = capsys.readouterr().out.splitlines()
    payload = json.loads(output[-1])
    assert payload["error"] == "session_not_new"
    assert path.read_bytes() == before
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert {event["run_id"] for event in events} == {"original-run"}


def test_interactive_test_marks_external_edit_as_verification_conflict(capsys, monkeypatch, tmp_path: Path):
    class EditingInput:
        def __init__(self):
            self.lines = iter(["inspect calculator", "/mode act", "fix calculator", "/test", "/quit"])
            self.edited = False

        def __iter__(self):
            return self

        def __next__(self):
            line = next(self.lines)
            if line == "/test" and not self.edited:
                target = tmp_path / "demo_calculator.py"
                target.write_text(target.read_text(encoding="utf-8") + "# external but tests still pass\n", encoding="utf-8")
                self.edited = True
            return line

    monkeypatch.setattr("sys.stdin", EditingInput())
    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--auto-approve", "--json"]) == 0
    payloads = [json.loads(line).get("payload") for line in capsys.readouterr().out.splitlines()]
    verification = next(payload["verification"] for payload in payloads if isinstance(payload, dict) and "verification" in payload)
    assert verification["ok"] is False
    assert verification["conflict"] is True
    assert verification["changed_files"] == ["demo_calculator.py"]


def test_interactive_test_preserves_quoted_command_arguments(capsys, monkeypatch, tmp_path: Path):
    lines = iter([
        "inspect calculator",
        "/mode act",
        "fix calculator",
        '/test python -c "print(\'quoted value\')"',
        "/quit",
    ])
    monkeypatch.setattr("sys.stdin", lines)

    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--auto-approve", "--json"]) == 0

    payloads = [json.loads(line).get("payload") for line in capsys.readouterr().out.splitlines()]
    verification = next(payload["verification"] for payload in payloads if isinstance(payload, dict) and "verification" in payload)
    assert verification["ok"] is True
    assert "quoted value" in verification["stdout"]


def test_interactive_test_accepts_a_single_quoted_complete_command(capsys, monkeypatch, tmp_path: Path):
    lines = iter(["inspect calculator", "/mode act", '/test "python -c \\\"print(42)\\\""', "/quit"])
    monkeypatch.setattr("sys.stdin", lines)

    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--auto-approve", "--json"]) == 0

    payloads = [json.loads(line).get("payload") for line in capsys.readouterr().out.splitlines()]
    verification = next(payload["verification"] for payload in payloads if isinstance(payload, dict) and "verification" in payload)
    assert verification["ok"] is True and "42" in verification["stdout"]


def test_interactive_quit_persists_checkpoint(capsys, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("sys.stdin", iter(["inspect calculator", "/quit"]))

    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--json"]) == 0

    session = _latest_session(tmp_path)
    checkpoint = session.with_suffix(".checkpoint.json")
    assert checkpoint.is_file()
    payloads = [json.loads(line).get("payload") for line in capsys.readouterr().out.splitlines()]
    quit_result = next(payload for payload in payloads if isinstance(payload, dict) and payload.get("stopped"))
    assert quit_result["checkpointed"] is True


def test_interactive_test_blocks_when_rules_change_after_planning(capsys, monkeypatch, tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Initial rule\n", encoding="utf-8")

    class ChangingInput:
        def __init__(self):
            self.lines = iter(["inspect calculator", "/mode act", "/test", "/quit"])
            self.changed = False

        def __iter__(self):
            return self

        def __next__(self):
            line = next(self.lines)
            if line == "/test" and not self.changed:
                (tmp_path / "AGENTS.md").write_text("Changed rule\n", encoding="utf-8")
                self.changed = True
            return line

    monkeypatch.setattr("sys.stdin", ChangingInput())
    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--auto-approve", "--json"]) == 0

    payloads = [json.loads(line).get("payload") for line in capsys.readouterr().out.splitlines()]
    verification = next(payload["verification"] for payload in payloads if isinstance(payload, dict) and "verification" in payload)
    assert verification["ok"] is False and verification["conflict"] is True
    assert verification["exit_code"] is None


def test_status_reports_corrupt_transaction_peer(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--json"]) == 0
    capsys.readouterr()
    manifest_dir = tmp_path / ".forgecode" / "transactions" / "manifests"
    (manifest_dir / "corrupt.json").write_text("{broken", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "status", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["transaction_issues"] and "corrupt.json" in payload["transaction_issues"][0]


def test_session_export_of_mixed_run_stream_is_partial_and_nonzero(capsys, tmp_path: Path):
    from forgecode.storage import SessionStore

    path = tmp_path / ".forgecode" / "sessions" / "mixed.jsonl"
    store = SessionStore(path, run_id="run-one")
    store.append("one", {})
    store.append("two", {})
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    second = json.loads(raw_lines[1])
    second["run_id"] = "run-two"
    path.write_text(raw_lines[0] + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    assert main(["--workspace", str(tmp_path), "session", "export", "mixed", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"] and "mixed run_id" in payload["issues"][0]["message"]
    assert payload["events_jsonl"]
