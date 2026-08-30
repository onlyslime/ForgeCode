import json
import os
from pathlib import Path

import pytest

from forgecode.agent import LifecycleError, RunLifecycle, RunState
from forgecode.storage import SessionFormatError, SessionStore
from forgecode.storage import CheckpointStore, TransactionError, TransactionStore
from forgecode.context import ContextIndex, ContextIndexError
from forgecode.review import ReviewArtifactError, import_review
from forgecode.security import WorkspaceGuard, bounded_json_loads


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
    with pytest.raises(SessionFormatError, match="cannot append"):
        store.append("would_be_unsafe", {})


def test_session_read_detects_regular_file_replacement_by_identity(monkeypatch, tmp_path: Path):
    """A same-size/timestamp replacement must not cross the audit boundary."""
    path = tmp_path / "replacement.jsonl"
    store = SessionStore(path, run_id="run-1")
    store.append("ok", {})
    original_stat = Path.stat
    calls = {"target": 0}

    def replacement_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == path:
            calls["target"] += 1
            if calls["target"] == 2:
                values = list(result)
                values[1] = values[1] + 1  # st_ino on supported platforms
                return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", replacement_stat)
    result = store.read_with_issues()
    assert any("changed while it was read" in issue.message for issue in result.issues)


def test_session_append_rejects_mixed_run_identity(tmp_path: Path):
    path = tmp_path / "mixed.jsonl"
    first = SessionStore(path, run_id="run-1")
    first.append("one", {})
    second = SessionStore(path, run_id="run-2")
    with pytest.raises(SessionFormatError, match="different or mixed run id"):
        second.append("two", {})


def test_session_normalizes_odd_values_and_redacts_nested_secrets(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", secrets=["top-secret"])
    cycle = []
    cycle.append(cycle)
    event = store.append("odd", {"path": Path("x"), "bytes": b"abc", "nan": float("nan"), "cycle": cycle, "message": "top-secret"})
    text = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert "top-secret" not in text
    assert event.payload["bytes"].startswith("[bytes omitted")
    assert "circular" in event.payload["cycle"][0]


def test_session_read_rejects_non_finite_json_payload(tmp_path: Path):
    path = tmp_path / "nonfinite.jsonl"
    path.write_text(
        '{"kind":"x","payload":{"value":NaN},"timestamp":"2026-01-01T00:00:00+00:00",'
        '"schema_version":1,"run_id":"run","sequence":1}\n',
        encoding="utf-8",
    )
    result = SessionStore(path).read_with_issues()
    assert not result.events and any("non-finite" in issue.message for issue in result.issues)
    with pytest.raises(SessionFormatError, match="non-finite"):
        SessionStore(path).read_with_issues(strict=True)


def test_session_read_reports_deep_json_without_recursion_error(tmp_path: Path):
    """Untrusted nesting is diagnosed during inspection, not process-crashed."""
    path = tmp_path / "deep.jsonl"
    # Build a valid event whose payload is far beyond the defensive shape
    # budget.  The constructor also scans existing lines, so exercise that
    # path explicitly rather than only calling read_with_issues afterwards.
    payload = (
        '{"schema_version":1,"kind":"x","payload":'
        + ("{\"x\":" * 1_000)
        + "0"
        + ("}" * 1_000)
        + ',"timestamp":"2026-01-01T00:00:00+00:00","run_id":"r","sequence":1}'
    )
    path.write_text(payload + "\n", encoding="utf-8")

    store = SessionStore(path)
    result = store.read_with_issues()

    assert not result.events
    assert any("nesting" in issue.message for issue in result.issues)
    with pytest.raises(SessionFormatError, match="nesting"):
        store.read_with_issues(strict=True)


def test_runtime_json_readers_fail_closed_on_deep_nesting(tmp_path: Path):
    """All durable JSON readers map hostile nesting to their public errors."""
    nested = "{\"x\":" * 1_000 + "0" + "}" * 1_000

    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(nested, encoding="utf-8")
    with pytest.raises(ValueError, match="nesting"):
        CheckpointStore(checkpoint_path).load()

    guard = WorkspaceGuard(tmp_path)
    manifest_dir = tmp_path / ".forgecode" / "transactions" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "deep.json").write_text(nested, encoding="utf-8")
    with pytest.raises(TransactionError, match="unreadable|nesting"):
        TransactionStore(guard).load("deep")

    index_path = tmp_path / ".forgecode" / "context-index.json"
    index_path.write_text(nested, encoding="utf-8")
    index = ContextIndex(guard)
    # A corrupt cache is rebuilt by the normal entries path, but must never
    # leak the decoder's RecursionError.
    try:
        index.entries()
    except ContextIndexError as exc:
        assert "nesting" in str(exc).lower() or "read context index" in str(exc).lower()

    artifact = tmp_path / "review.json"
    artifact.write_text(nested, encoding="utf-8")
    with pytest.raises(ReviewArtifactError, match="unreadable|nesting"):
        import_review(artifact, guard)


def test_bounded_json_rejects_flat_scalar_floods_before_recursive_decode():
    value = "[" + ",".join("0" for _ in range(100_001)) + "]"
    with pytest.raises(ValueError, match="node safety limit"):
        bounded_json_loads(value)


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


def test_session_inspection_reports_mixed_run_ids_without_append(tmp_path: Path):
    path = tmp_path / "mixed-runs.jsonl"
    first = SessionStore(path, run_id="run-one")
    first.append("one", {})
    first.append("two", {})
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(raw_lines[1])
    raw["run_id"] = "run-two"
    path.write_text(raw_lines[0] + "\n" + json.dumps(raw) + "\n", encoding="utf-8")

    result = SessionStore(path).read_with_issues()

    assert any("mixed run_id" in issue.message for issue in result.issues)
    assert any("inspect-only" in issue.message for issue in result.issues)


def test_session_inspection_reports_legacy_v1_mixed_stream_and_strict_rejects(tmp_path: Path):
    path = tmp_path / "mixed-schema.jsonl"
    legacy = {"kind": "user_message", "payload": {"content": "old"}, "timestamp": "2026-01-01T00:00:00+00:00"}
    modern = {"kind": "run_created", "payload": {}, "timestamp": "2026-01-01T00:00:01+00:00", "schema_version": 1, "run_id": "run-one", "sequence": 1}
    path.write_text(json.dumps(legacy) + "\n" + json.dumps(modern) + "\n", encoding="utf-8")

    store = SessionStore(path)
    result = store.read_with_issues()
    assert any("legacy and v1" in issue.message for issue in result.issues)
    with pytest.raises(SessionFormatError, match="legacy and v1"):
        store.read_with_issues(strict=True)


def test_session_inspection_reports_requested_run_identity_mismatch(tmp_path: Path):
    path = tmp_path / "identity.jsonl"
    SessionStore(path, run_id="run-one").append("one", {})

    result = SessionStore(path, run_id="run-two").read_with_issues()

    assert any("requested store run_id" in issue.message for issue in result.issues)


def test_session_inspection_reports_invalid_terminal_transition(tmp_path: Path):
    path = tmp_path / "terminal.jsonl"
    store = SessionStore(path, run_id="run-one")
    store.append("state_transition", {"from": "created", "to": "discovering"})
    store.append("state_transition", {"from": "discovering", "to": "planning"})
    store.append("state_transition", {"from": "planning", "to": "completed"})
    # Bypass append's safety guard to model a forged on-disk terminal event.
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(raw_lines[-1])
    forged["sequence"] = 4
    forged["payload"] = {"from": "completed", "to": "acting"}
    path.write_text("\n".join(raw_lines + [json.dumps(forged)]) + "\n", encoding="utf-8")

    result = SessionStore(path).read_with_issues()

    assert any("invalid terminal" in issue.message for issue in result.issues)
