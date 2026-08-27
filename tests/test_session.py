from pathlib import Path

from forgecode.storage import SessionStore


def test_session_store_appends_jsonl(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions" / "run.jsonl")
    store.append("user_message", {"content": "hello"})
    events = list(store.read())
    assert len(events) == 1
    assert events[0].kind == "user_message"
    assert events[0].payload["content"] == "hello"
