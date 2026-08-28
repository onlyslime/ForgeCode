from __future__ import annotations

import json

from forgecode.rpc import serve_lines


def _call(request: dict) -> dict:
    output = list(serve_lines([json.dumps(request)]))
    assert output
    return json.loads(output[-1])


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


def test_rpc_session_run_requires_handle():
    payload = _call({"method": "session.run", "params": {"prompt": "hello"}})
    assert payload["ok"] is False
    assert "requires session handle" in payload["error"]["message"]


def test_rpc_session_control_is_sequenced_and_replayable(tmp_path):
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})
    handle = opened["data"]["session"]
    paused = _call({"id": "p", "method": "session.pause", "params": {"session": handle}})
    assert paused["data"]["state"] == "paused"
    resumed = _call({"id": "r", "method": "session.resume", "params": {"session": handle}})
    assert resumed["data"]["sequence"] == 2
    events = _call({"method": "session.events", "params": {"session": handle}})
    assert [item["type"] for item in events["data"]["events"]] == ["pause", "resume"]
    delta = _call({"method": "session.events", "params": {"session": handle, "after": 1, "limit": 1}})
    assert [item["type"] for item in delta["data"]["events"]] == ["resume"]
    assert delta["data"]["next_sequence"] == 2


def test_rpc_request_id_replays_without_reapplying_control(tmp_path):
    opened = _call({"id": "open-replay", "method": "session.open", "params": {"workspace": str(tmp_path)}})
    handle = opened["data"]["session"]
    first = _call({"id": "pause-replay", "method": "session.pause", "params": {"session": handle}})
    second = _call({"id": "pause-replay", "method": "session.pause", "params": {"session": handle}})
    assert first == second
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["sequence"] == 1


def test_rpc_session_approval_requires_boolean_and_updates_state(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    denied = _call({"method": "session.approval", "params": {"session": handle, "approved": False}})
    assert denied["data"]["state"] == "approval_denied"
    assert denied["data"]["sequence"] == 1
    invalid = _call({"method": "session.approval", "params": {"session": handle, "approved": "no"}})
    assert invalid["ok"] is False


def test_rpc_session_run_updates_handle_state(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    result = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello", "demo": True}})
    assert result["method"] == "session.run"
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["state"] in {"completed", "failed", "cancelled"}


def test_rpc_idempotency_caches_multi_event_response(tmp_path):
    request = {"id": "doctor-replay", "method": "doctor", "params": {}}
    first = list(serve_lines([json.dumps(request)]))
    second = list(serve_lines([json.dumps(request)]))
    assert first == second
    assert json.loads(second[-1])["id"] == "doctor-replay"
    conflict = list(serve_lines([json.dumps({"id": "doctor-replay", "method": "provider.list", "params": {}})]))
    assert json.loads(conflict[-1])["error"]["message"].startswith("request id was already used")


def test_rpc_act_handle_fails_closed_after_trust_revoke(tmp_path):
    _call({"method": "trust.grant", "params": {"workspace": str(tmp_path)}})
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "mode": "act"}})
    handle = opened["data"]["session"]
    _call({"method": "trust.revoke", "params": {"workspace": str(tmp_path)}})
    denied = _call({"method": "session.status", "params": {"session": handle}})
    assert denied["ok"] is False
    assert denied["error"]["code"] == "trust_revoked"
    assert "trust" in denied["error"]["message"]


def test_rpc_session_status_exposes_bounded_lifecycle_metadata(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["workspace"] == str(tmp_path)
    assert "created_monotonic" not in status["data"]


def test_rpc_session_event_history_is_bounded(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    for _ in range(520):
        _call({"method": "session.pause", "params": {"session": handle}})
        _call({"method": "session.resume", "params": {"session": handle}})
    events = _call({"method": "session.events", "params": {"session": handle, "limit": 100}})["data"]["events"]
    assert len(events) == 100
    assert events[0]["sequence"] > 512


def test_rpc_session_handle_can_be_recovered_from_workspace_metadata(tmp_path):
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})
    handle = opened["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    recovered = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    assert recovered["data"]["session"] == handle
    assert recovered["data"]["recovered"] is True


def test_rpc_recovery_restores_event_cursor(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    _call({"method": "session.pause", "params": {"session": handle}})
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    events = _call({"method": "session.events", "params": {"session": handle, "after": 0}})
    assert events["data"]["events"][0]["type"] == "pause"


def test_rpc_act_recovery_rejects_revoked_workspace(tmp_path):
    _call({"method": "trust.grant", "params": {"workspace": str(tmp_path)}})
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "mode": "act"}})
    handle = opened["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    _call({"method": "trust.revoke", "params": {"workspace": str(tmp_path)}})
    recovered = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    assert recovered["ok"] is False
    assert recovered["error"]["code"] == "trust_revoked"
