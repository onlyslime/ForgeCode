from pathlib import Path
from dataclasses import dataclass

import pytest

from forgecode.storage import SessionStore
from forgecode.security.redaction import redact_value


def test_session_store_appends_jsonl(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions" / "run.jsonl")
    store.append("user_message", {"content": "hello"})
    events = list(store.read())
    assert len(events) == 1
    assert events[0].kind == "user_message"
    assert events[0].payload["content"] == "hello"


def test_session_store_bounds_large_values(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", max_event_chars=1_000)
    store.append("tool_result", {"output": "x" * 50_000, "items": list(range(500))})
    raw = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert len(raw) < 2_000
    event = next(store.read())
    assert event.payload["truncated"] is True


def test_session_redacts_nested_credential_shapes(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl")
    store.append(
        "provider_error",
        {
            "nested": [{"Authorization": "Bearer abc123", "api-key": "key-value"}],
            "message": "token=inline-secret password: 'pw-value'",
        },
    )
    raw = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    for value in ("abc123", "key-value", "inline-secret", "pw-value"):
        assert value not in raw
    assert "REDACTED" in raw


def test_session_rejects_an_event_limit_too_small_for_valid_jsonl(tmp_path: Path):
    with pytest.raises(ValueError, match="at least 128"):
        SessionStore(tmp_path / "run.jsonl", max_event_chars=127)


def test_redaction_walker_handles_dataclass_bytes_exception_and_cycles():
    @dataclass
    class Metadata:
        token: str
        path: Path

    cycle = []
    cycle.append(cycle)
    value = {"metadata": Metadata("secret-token", Path("src/a.py")), "bytes": b"abc", "error": RuntimeError("password=hidden"), "cycle": cycle}
    redacted = redact_value(value, secrets=("secret-token",))
    assert redacted["metadata"]["token"] == "[REDACTED]"
    assert redacted["metadata"]["path"] == "src/a.py"
    assert redacted["bytes"].startswith("[bytes omitted")
    assert redacted["error"]["type"] == "RuntimeError"
    assert "hidden" not in str(redacted["error"])
    assert "circular" in redacted["cycle"][0]


def test_redaction_walker_normalizes_nonfinite_numbers():
    redacted = redact_value({"nan": float("nan"), "inf": float("inf"), "ok": 1.5})
    assert redacted["nan"] == "[non-finite number omitted]"
    assert redacted["inf"] == "[non-finite number omitted]"
    assert redacted["ok"] == 1.5
