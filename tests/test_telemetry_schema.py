from __future__ import annotations

import json

from forgecode.telemetry import Telemetry


def test_telemetry_schema_drops_sensitive_and_unbounded_fields(tmp_path):
    telemetry = Telemetry(tmp_path, mode="local")
    assert telemetry.record("tool_finished", prompt="secret prompt", command="rm -rf", stdout="output", safe_count=3, nested={"x": 1}, long_value="x" * 257)
    record = json.loads((tmp_path / ".forgecode" / "telemetry.jsonl").read_text(encoding="utf-8"))
    assert record["schema_version"] == 1
    assert record["safe_count"] == 3
    assert record["dropped_fields"] == 5
    assert "prompt" not in record and "command" not in record and "stdout" not in record


def test_telemetry_offline_never_creates_records(tmp_path):
    telemetry = Telemetry(tmp_path, mode="local", offline=True)
    assert telemetry.record("event", value=1) is False
    assert not (tmp_path / ".forgecode" / "telemetry.jsonl").exists()


def test_telemetry_event_names_are_safe_tokens(tmp_path):
    telemetry = Telemetry(tmp_path, mode="local")
    telemetry.record("prompt: do not log /workspace/secret.txt", value=True)
    record = json.loads((tmp_path / ".forgecode" / "telemetry.jsonl").read_text(encoding="utf-8"))
    assert record["event"] == "prompt_do_not_log_workspace_secret.txt"
    assert ":" not in record["event"] and "/" not in record["event"]


def test_telemetry_classifies_event_families_and_flags_unknown(tmp_path):
    telemetry = Telemetry(tmp_path, mode="local")
    telemetry.record("provider_error", code="timeout")
    telemetry.record("mystery_event", value=True)
    records = [json.loads(line) for line in (tmp_path / ".forgecode" / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[0]["event_family"] == "provider"
    assert records[1]["event_family"] == "unknown"
    assert records[1]["audit_warning"] == "unclassified_event"
