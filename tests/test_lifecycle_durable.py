import json
from pathlib import Path

import pytest

from forgecode.agent import LifecycleError, RunLifecycle, RunState
from forgecode.storage import SessionFormatError, SessionStore


def test_lifecycle_has_checked_transitions_and_terminal_states():
    lifecycle = RunLifecycle()
    assert lifecycle.state is RunState.CREATED
    lifecycle.transition("discovering")
    lifecycle.transition(RunState.PLANNING)
    lifecycle.transition(RunState.COMPLETED)
    assert lifecycle.terminal
    with pytest.raises(LifecycleError, match="invalid run state transition"):
        lifecycle.transition(RunState.ACTING)


def test_session_envelope_sequences_and_metadata_are_durable(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", run_id="run-1", mode="act")
    first = store.append("run_created", {"value": "hello"})
    second = store.append("tool_call", {"path": Path("a.txt")}, operation_id="call-1", outcome="started")
    assert (first.schema_version, first.run_id, first.sequence, first.mode) == (1, "run-1", 1, "act")
    assert second.sequence == 2 and second.operation_id == "call-1"
    raw = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [entry["sequence"] for entry in raw] == [1, 2]
    assert [event.sequence for event in store.read()] == [1, 2]


def test_session_safe_partial_read_reports_corrupt_line(tmp_path: Path):
    path = tmp_path / "run.jsonl"
    store = SessionStore(path, run_id="run-1")
    store.append("ok", {})
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"kind": "broken"\n')
    result = store.read_with_issues()
    assert len(result.events) == 1
    assert result.issues and result.issues[0].line == 2
    with pytest.raises(SessionFormatError, match="line 2"):
        store.read_with_issues(strict=True)


def test_session_normalizes_odd_values_and_redacts_nested_secrets(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", secrets=["top-secret"])
    cycle = []
    cycle.append(cycle)
    event = store.append("odd", {"path": Path("x"), "bytes": b"abc", "nan": float("nan"), "cycle": cycle, "message": "top-secret"})
    text = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert "top-secret" not in text
    assert event.payload["bytes"].startswith("[bytes omitted")
    assert "circular" in event.payload["cycle"][0]


def test_new_store_continues_sequence_after_existing_events(tmp_path: Path):
    path = tmp_path / "run.jsonl"
    first = SessionStore(path, run_id="run-1")
    first.append("one", {})
    second = SessionStore(path, run_id="run-1")
    assert second.append("two", {}).sequence == 2


def test_session_safe_partial_read_is_bounded(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", max_event_chars=256)
    store.append("large", {"output": "x" * 100_000})
    assert len((tmp_path / "run.jsonl").read_text(encoding="utf-8")) <= 256


def test_session_reports_sequence_gap(tmp_path: Path):
    path = tmp_path / "gap.jsonl"
    first = SessionStore(path, run_id="r")
    first.append("one", {})
    first.append("two", {})
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(raw_lines[1])
    raw["sequence"] = 3
    path.write_text(raw_lines[0] + "\n" + json.dumps(raw) + "\n", encoding="utf-8")
    result = SessionStore(path).read_with_issues()
    assert any("gap" in issue.message for issue in result.issues)
