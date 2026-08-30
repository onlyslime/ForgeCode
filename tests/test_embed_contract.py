from __future__ import annotations

import pytest

from forgecode.embed import ForgeCodeError, config_profiles, provider_list, provider_health, config_policy, invoke, rpc_describe, login, session_open, session_run, session_inspect, session_status, session_events, session_result, session_wait, session_tree, session_cancel, session_pause, session_resume, session_approval, stream


def test_rpc_describe_embedding_returns_capabilities() -> None:
    rows = rpc_describe()
    assert rows and rows[0]["kind"] == "capabilities"


def test_package_exports_rpc_describe_embedding() -> None:
    import forgecode
    assert "rpc_describe_embedded" in forgecode.__all__
import forgecode


def test_embed_raise_for_status_preserves_envelope(tmp_path):
    with pytest.raises(ForgeCodeError) as caught:
        invoke(["--workspace", str(tmp_path), "login", "--provider", "unknown"], raise_for_status=True)
    assert caught.value.code == "unsupported_provider"
    assert caught.value.envelope and caught.value.envelope["ok"] is False


def test_embed_invoke_validates_response_limit():
    with pytest.raises(ValueError):
        invoke(["doctor"], max_response_bytes=0)
    with pytest.raises(ValueError):
        invoke(["doctor"], max_response_bytes=True)


def test_embed_invoke_validates_request_id():
    with pytest.raises(ValueError):
        invoke(["doctor"], request_id="")
    with pytest.raises(ValueError):
        invoke(["doctor"], request_id="x" * 257)
    with pytest.raises(ValueError):
        invoke(["doctor"], request_id=True)


def test_embed_invoke_validates_argv_bounds():
    with pytest.raises(ValueError):
        invoke(["doctor"] * 129)
    with pytest.raises(ValueError):
        invoke(["x" * 1_001])


def test_embed_stream_validates_request_objects_and_size():
    with pytest.raises(ValueError):
        list(stream(["not-an-object"]))
    with pytest.raises(ValueError):
        list(stream([{"payload": "x" * 1_000_001}]))
    with pytest.raises(ValueError):
        list(stream([{"value": float("nan")}]))
    with pytest.raises(ValueError):
        list(stream([{"id": True, "method": "doctor"}]))
    with pytest.raises(ValueError):
        list(stream([{"id": "x" * 257, "method": "doctor"}]))


def test_embed_stream_strict_mode_raises_on_failed_rpc():
    with pytest.raises(ForgeCodeError):
        list(stream([{"method": "not-supported"}], raise_for_status=True))


def test_embed_stream_bounds_response_items():
    with pytest.raises(ForgeCodeError) as caught:
        list(stream([{"method": "doctor"}, {"method": "doctor"}], max_items=1))
    assert caught.value.code == "output_limit"
    with pytest.raises(ValueError):
        list(stream([], max_items="many"))
    with pytest.raises(ValueError):
        list(stream([], max_response_bytes="large"))


def test_embed_stream_invalid_json_uses_typed_error(monkeypatch):
    monkeypatch.setattr("forgecode.embed.serve_lines", lambda _lines: iter(["{broken-json"]))
    with pytest.raises(ForgeCodeError) as caught:
        list(stream([{"method": "doctor"}]))
    assert caught.value.code == "invalid_json"


def test_embed_session_result_validates_handle():
    with pytest.raises(ValueError):
        session_result("")
    with pytest.raises(ValueError):
        session_result("x\ny")
    assert forgecode.session_result_embedded is session_result
    assert forgecode.session_open_embedded is session_open
    assert forgecode.session_run_embedded is session_run
    assert forgecode.session_inspect_embedded is session_inspect
    assert forgecode.session_status_embedded is session_status
    assert forgecode.session_events_embedded is session_events
    assert forgecode.session_wait_embedded is session_wait
    assert forgecode.session_tree_embedded is session_tree
    assert forgecode.session_cancel_embedded is session_cancel
    assert forgecode.session_pause_embedded is session_pause
    assert forgecode.session_resume_embedded is session_resume
    assert forgecode.session_approval_embedded is session_approval
    assert forgecode.config_policy_embedded is config_policy
    assert forgecode.config_profiles_embedded is config_profiles
    assert forgecode.provider_list_embedded is provider_list
    assert forgecode.provider_health_embedded is provider_health
    assert forgecode.login_embedded is login
    with pytest.raises(ValueError):
        session_wait("x", timeout=61)
    with pytest.raises(ValueError):
        session_wait("x", timeout=float("nan"))
    with pytest.raises(ValueError):
        session_events("x", wait=float("nan"))
    with pytest.raises(ValueError):
        session_wait("x", workspace="bad\npath")
    with pytest.raises(ValueError):
        session_tree(limit=201)
    with pytest.raises(ValueError):
        config_policy(no_tools="yes")
    with pytest.raises(ValueError):
        config_policy(workspace="bad\npath")
    with pytest.raises(ValueError):
        config_profiles(workspace="bad\npath")
    with pytest.raises(ValueError):
        login(api_key_env="bad\nenv")
    with pytest.raises(ValueError):
        session_cancel("x", workspace="bad\npath")
    with pytest.raises(ValueError):
        session_approval("x", "yes")
    with pytest.raises(ValueError):
        session_events("x", after=-1)
    with pytest.raises(ValueError):
        session_events("x", wait=31)
    with pytest.raises(ValueError):
        session_events("x", event_type="bad\nkind")
    with pytest.raises(ValueError):
        session_inspect("x", workspace="bad\npath")


def test_embed_session_controls_use_rpc_envelopes(tmp_path):
    opened = session_open(workspace=str(tmp_path), raise_for_status=True)
    handle = opened[-1]["data"]["session"]
    cancelled = session_cancel(handle, workspace=str(tmp_path), raise_for_status=True)
    assert cancelled[-1]["command"] == "session.cancel"
    assert cancelled[-1]["data"]["state"] == "cancelled"
