from forgecode.models import ProviderError


def test_provider_error_to_dict_is_bounded_and_structured():
    error = ProviderError("x" * 2_000, category="stream_incomplete", retryable=True, status_code=503, attempt=2, request_id="req-1", unresolved=True)
    payload = error.to_dict()
    assert payload["category"] == "stream_incomplete"
    assert payload["retryable"] is True
    assert payload["status_code"] == 503
    assert len(payload["message"]) == 500
    assert payload["unresolved"] is True
