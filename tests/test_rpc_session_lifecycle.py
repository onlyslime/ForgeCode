from __future__ import annotations

import json

from forgecode.rpc import serve_lines


def _call(request: dict) -> dict:
    output = list(serve_lines([json.dumps(request)]))
    assert len(output) == 1
    return json.loads(output[0])


def test_rpc_session_open_status_close_lifecycle(tmp_path):
    opened = _call({"id": 1, "method": "session.open", "params": {"workspace": str(tmp_path), "mode": "plan"}})
    assert opened["ok"] is True
    handle = opened["data"]["session"]
    assert len(handle) == 32

    status = _call({"id": 2, "method": "session.status", "params": {"session": handle}})
    assert status["data"]["closed"] is False
    assert status["data"]["mode"] == "plan"

    closed = _call({"id": 3, "method": "session.close", "params": {"session": handle}})
    assert closed["data"]["closed"] is True

    missing = _call({"id": 4, "method": "session.status", "params": {"session": handle}})
    assert missing["ok"] is False
    assert missing["error"]["code"] == "invalid_request"


def test_rpc_session_open_validates_bounds():
    payload = _call({"method": "session.open", "params": {"mode": "unsafe"}})
    assert payload["ok"] is False
    assert "plan or act" in payload["error"]["message"]
