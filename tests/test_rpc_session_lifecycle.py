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


def test_rpc_request_id_is_bounded_and_newline_safe():
    oversized = {"id": "x" * 257, "argv": ["doctor"]}
    response = json.loads(next(iter(serve_lines([json.dumps(oversized)]))))
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_rpc_config_profiles_is_programmatically_discoverable(tmp_path):
    payload = _call({"method": "config.profiles", "params": {"workspace": str(tmp_path)}})
    assert payload["ok"] is True
    assert payload["command"] == "config profiles"


def test_rpc_config_profiles_honors_workspace_parameter(tmp_path):
    payload = _call({"method": "config.profiles", "params": {"workspace": str(tmp_path)}})
    assert payload["ok"] is True
    assert payload.get("workspace") == str(tmp_path)


def test_rpc_diagnostics_reject_missing_workspace(tmp_path):
    payload = _call({"method": "config.profiles", "params": {"workspace": str(tmp_path / "missing")}})
    assert payload["ok"] is False
    assert "existing directory" in payload["error"]["message"]


def test_rpc_request_line_is_bounded():
    response = json.loads(next(iter(serve_lines(["{" + "x" * 1_048_576]))))
    assert response["ok"] is False
    assert "request_too_large" in response["error"]["message"]
    assert response["error"]["code"] == "request_too_large"


def test_rpc_session_open_validates_bounds():
    payload = _call({"method": "session.open", "params": {"mode": "unsafe"}})
    assert payload["ok"] is False
    assert "plan or act" in payload["error"]["message"]


def test_rpc_session_open_canonicalizes_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _call({"method": "session.open", "params": {"workspace": "."}})
    assert payload["data"]["workspace"] == str(tmp_path.resolve())


def test_rpc_session_persistence_tolerates_unsupported_fsync(tmp_path, monkeypatch):
    import forgecode.rpc as rpc
    monkeypatch.setattr(rpc.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("unsupported")))
    payload = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})
    assert payload["ok"] is True


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


def test_rpc_background_run_allows_control_while_worker_is_active(tmp_path, monkeypatch):
    import threading
    import time
    from forgecode import rpc

    started = threading.Event()
    release = threading.Event()
    def slow_main(_argv):
        started.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(rpc, "main", slow_main)
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    accepted = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello", "background": True}})
    assert accepted["data"]["accepted"] is True
    assert started.wait(1)
    cancelled = _call({"method": "session.cancel", "params": {"session": handle}})
    assert cancelled["data"]["cancel_requested"] is True
    release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        status = _call({"method": "session.status", "params": {"session": handle}})
        if status["data"]["state"] == "cancelled":
            break
        time.sleep(0.02)
    assert status["data"]["state"] == "cancelled"


def test_rpc_background_run_persists_structured_result(tmp_path, monkeypatch):
    from forgecode import rpc
    monkeypatch.setattr(rpc, "main", lambda _argv: print(json.dumps({"ok": True, "data": {"answer": 42}})) or 0)
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    accepted = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello", "background": True}})
    assert accepted["data"]["accepted"] is True
    import time
    for _ in range(50):
        status = _call({"method": "session.status", "params": {"session": handle}})
        if status["data"]["state"] == "completed":
            break
        time.sleep(0.01)
    assert status["data"]["result"][0]["data"]["answer"] == 42
    result = _call({"method": "session.result", "params": {"session": handle}})
    assert result["data"]["result"][0]["data"]["answer"] == 42
    recovered = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    assert recovered["data"]["recovered"] is True


def test_rpc_isolated_background_run_can_be_terminated(tmp_path):
    import time
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    accepted = _call({"method": "session.run", "params": {"session": handle, "prompt": "wait", "background": True, "isolate": True, "demo": True}})
    assert accepted["data"]["accepted"] is True
    cancelled = _call({"method": "session.cancel", "params": {"session": handle}})
    assert cancelled["data"]["cancel_requested"] is True
    for _ in range(100):
        status = _call({"method": "session.status", "params": {"session": handle}})
        if status["data"]["state"] == "cancelled": break
        time.sleep(0.01)
    assert status["data"]["state"] == "cancelled"


def test_rpc_isolated_worker_start_failure_releases_handle(tmp_path, monkeypatch):
    from forgecode import rpc
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    rpc._RPC_SESSIONS[handle]["state"] = "running"
    def fail(*_args, **_kwargs):
        raise OSError("spawn denied")
    monkeypatch.setattr(rpc.subprocess, "Popen", fail)
    rpc._isolated_session_run(handle, ["run", "hello", "--jsonl"])
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["state"] == "failed"


def test_rpc_output_truncation_is_warning_not_failure(tmp_path):
    import forgecode.rpc as rpc
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    rpc._RPC_SESSIONS[handle]["state"] = "running"
    rpc._finish_background_session(handle, 0, json.dumps({"ok": True}), "output_truncated")
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["state"] == "completed"


def test_rpc_session_run_rejects_denied_approval(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    _call({"method": "session.approval", "params": {"session": handle, "approved": False}})
    denied = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello", "demo": True}})
    assert denied["ok"] is False
    assert "approval" in denied["error"]["message"]


def test_rpc_session_run_rejects_concurrent_busy_handle(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS[handle]["state"] = "running"
    busy = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello", "demo": True}})
    assert busy["ok"] is False
    assert "busy" in busy["error"]["message"]


def test_rpc_close_rejects_active_run(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS[handle]["state"] = "running"
    closed = _call({"method": "session.close", "params": {"session": handle}})
    assert closed["ok"] is False
    assert "busy" in closed["error"]["message"]
    assert closed["error"]["code"] == "session_busy"


def test_rpc_close_rejects_paused_worker(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS[handle]["state"] = "paused"
    closed = _call({"method": "session.close", "params": {"session": handle}})
    assert closed["ok"] is False
    assert closed["error"]["code"] == "session_busy"


def test_rpc_close_waits_for_cancelled_process_exit(tmp_path):
    import subprocess
    from forgecode import rpc
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    process = subprocess.Popen(["cmd", "/c", "ping -n 2 127.0.0.1 > nul"])
    rpc._RPC_SESSIONS[handle]["state"] = "cancelled"
    rpc._RPC_SESSIONS[handle]["process"] = process
    try:
        closed = _call({"method": "session.close", "params": {"session": handle}})
        assert closed["ok"] is False
        assert closed["error"]["code"] == "session_busy"
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_rpc_cancel_records_process_termination(tmp_path):
    import subprocess
    from forgecode import rpc
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    process = subprocess.Popen(["cmd", "/c", "ping -n 2 127.0.0.1 > nul"])
    rpc._RPC_SESSIONS[handle]["state"] = "running"
    rpc._RPC_SESSIONS[handle]["process"] = process
    try:
        cancelled = _call({"method": "session.cancel", "params": {"session": handle}})
        assert cancelled["ok"] is True
        events = _call({"method": "session.events", "params": {"session": handle}})["data"]["events"]
        assert events[-1].get("termination") in {"terminate", "kill", "unresolved"}
    finally:
        if process.poll() is None: process.kill()
        process.wait(timeout=3)


def test_rpc_session_run_rejects_cancelled_handle(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    _call({"method": "session.cancel", "params": {"session": handle}})
    result = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello", "demo": True}})
    assert result["ok"] is False
    assert "cancelled" in result["error"]["message"]
    assert result["error"]["code"] == "session_terminal"


def test_rpc_failed_run_releases_busy_state(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    from forgecode import rpc
    original = rpc.main
    rpc.main = lambda _argv: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        failed = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello", "demo": True}})
    finally:
        rpc.main = original
    assert failed["ok"] is False
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["state"] == "failed"


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


def test_rpc_act_cancel_remains_available_after_trust_revoke(tmp_path):
    _call({"method": "trust.grant", "params": {"workspace": str(tmp_path)}})
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "mode": "act"}})["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS[handle]["state"] = "running"
    _call({"method": "trust.revoke", "params": {"workspace": str(tmp_path)}})
    cancelled = _call({"method": "session.cancel", "params": {"session": handle}})
    assert cancelled["ok"] is True
    assert cancelled["data"]["cancel_requested"] is True


def test_rpc_act_result_remains_readable_after_trust_revoke(tmp_path):
    _call({"method": "trust.grant", "params": {"workspace": str(tmp_path)}})
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "mode": "act"}})["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS[handle]["result"] = [{"ok": True, "data": {"audit": "safe"}}]
    rpc._RPC_SESSIONS[handle]["state"] = "completed"
    _call({"method": "trust.revoke", "params": {"workspace": str(tmp_path)}})
    result = _call({"method": "session.result", "params": {"session": handle}})
    assert result["ok"] is True
    assert result["data"]["result"][0]["data"]["audit"] == "safe"


def test_rpc_untrusted_act_run_does_not_poison_handle_state(tmp_path):
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "mode": "act"}})
    handle = opened["data"]["session"]
    denied = _call({"method": "session.run", "params": {"session": handle, "prompt": "hello"}})
    assert denied["ok"] is False
    from forgecode import rpc
    assert rpc._RPC_SESSIONS[handle]["state"] == "idle"


def test_rpc_session_status_exposes_bounded_lifecycle_metadata(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["workspace"] == str(tmp_path)
    assert "created_monotonic" not in status["data"]


def test_rpc_cancel_exposes_auditable_cancel_request(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    cancelled = _call({"method": "session.cancel", "params": {"session": handle}})
    assert cancelled["data"]["state"] == "cancelled"
    assert cancelled["data"]["cancel_requested"] is True


def test_rpc_session_event_history_is_bounded(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    for _ in range(520):
        _call({"method": "session.pause", "params": {"session": handle}})
        _call({"method": "session.resume", "params": {"session": handle}})
    response = _call({"method": "session.events", "params": {"session": handle, "limit": 100}})
    data = response["data"]
    events = data["events"]
    assert len(events) == 100
    assert events[0]["sequence"] > 512
    assert data["oldest_sequence"] == events[0]["sequence"]
    assert data["truncated"] is True


def test_rpc_session_events_reports_cursor_is_current(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    _call({"method": "session.pause", "params": {"session": handle}})
    data = _call({"method": "session.events", "params": {"session": handle, "after": 0}})["data"]
    assert data["oldest_sequence"] == 1
    assert data["truncated"] is False


def test_rpc_session_handle_can_be_recovered_from_workspace_metadata(tmp_path):
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})
    handle = opened["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    recovered = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    assert recovered["data"]["session"] == handle
    assert recovered["data"]["recovered"] is True


def test_rpc_recovery_marks_orphaned_running_worker(tmp_path):
    import forgecode.rpc as rpc
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})
    handle = opened["data"]["session"]
    rpc._RPC_SESSIONS[handle]["state"] = "running"
    rpc._persist_session(handle, rpc._RPC_SESSIONS[handle])
    rpc._RPC_SESSIONS.pop(handle, None)
    recovered = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    assert recovered["data"]["state"] == "recovery_required"
    denied = _call({"method": "session.pause", "params": {"session": handle}})
    assert denied["ok"] is False
    assert "recovery" in denied["error"]["message"]


def test_rpc_recovery_open_is_idempotent_by_request_id(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    request = {"id": "recover-open", "method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}}
    first = list(serve_lines([json.dumps(request)]))
    second = list(serve_lines([json.dumps(request)]))
    assert first == second


def test_rpc_recovery_replay_cache_remains_bounded(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    for index in range(1030):
        request = {"id": f"recover-{index}", "method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}}
        list(serve_lines([json.dumps(request)]))
    assert len(rpc._RPC_REPLAYS) <= 1024
    assert len(rpc._RPC_FINGERPRINTS) <= 1024


def test_rpc_recovery_restores_event_cursor(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    _call({"method": "session.pause", "params": {"session": handle}})
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    events = _call({"method": "session.events", "params": {"session": handle, "after": 0}})
    assert events["data"]["events"][0]["type"] == "pause"


def test_rpc_recovery_restores_cancel_request_marker(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    _call({"method": "session.cancel", "params": {"session": handle}})
    from forgecode import rpc
    rpc._RPC_SESSIONS.pop(handle, None)
    restored = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    assert restored["ok"] is True
    assert restored["data"]["state"] == "cancelled"
    assert restored["data"]["cancel_requested"] is True
    status = _call({"method": "session.status", "params": {"session": handle}})
    assert status["data"]["cancel_requested"] is True


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


def test_rpc_session_close_removes_recovery_record(tmp_path):
    opened = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})
    handle = opened["data"]["session"]
    _call({"method": "session.close", "params": {"session": handle}})
    reopened = _call({"method": "session.open", "params": {"workspace": str(tmp_path), "session": handle}})
    assert reopened["ok"] is False
    assert "not recoverable" in reopened["error"]["message"]


def test_rpc_persisted_record_is_valid_json_and_contains_no_prompt(tmp_path):
    handle = _call({"method": "session.open", "params": {"workspace": str(tmp_path)}})["data"]["session"]
    record_path = tmp_path / ".forgecode" / "rpc-sessions" / f"{handle}.json"
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["workspace"] == str(tmp_path)
    assert "prompt" not in payload and "credential" not in payload
