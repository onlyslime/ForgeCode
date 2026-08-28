from __future__ import annotations

import pytest

from forgecode.embed import ForgeCodeError, invoke, stream


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
