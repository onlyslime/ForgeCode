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
