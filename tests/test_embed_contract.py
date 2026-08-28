from __future__ import annotations

import pytest

from forgecode.embed import ForgeCodeError, invoke, session_result, session_wait, session_tree, stream
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


def test_embed_stream_strict_mode_raises_on_failed_rpc():
    with pytest.raises(ForgeCodeError):
        list(stream([{"method": "not-supported"}], raise_for_status=True))


def test_embed_stream_bounds_response_items():
    with pytest.raises(ForgeCodeError) as caught:
        list(stream([{"method": "doctor"}, {"method": "doctor"}], max_items=1))
    assert caught.value.code == "output_limit"


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
    assert forgecode.session_wait_embedded is session_wait
    assert forgecode.session_tree_embedded is session_tree
    with pytest.raises(ValueError):
        session_wait("x", timeout=61)
    with pytest.raises(ValueError):
        session_wait("x", workspace="bad\npath")
    with pytest.raises(ValueError):
        session_tree(limit=201)
