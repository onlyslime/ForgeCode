from forgecode.models import ProviderError
from forgecode.models.openai_compatible import _error_message


def test_provider_error_to_dict_is_bounded_and_structured():
    error = ProviderError("x" * 2_000, category="stream_incomplete", retryable=True, status_code=503, attempt=2, request_id="req-1", unresolved=True)
    payload = error.to_dict()
    assert payload["category"] == "stream_incomplete"
    assert payload["retryable"] is True
    assert payload["status_code"] == 503
    assert len(payload["message"]) == 500
    assert payload["unresolved"] is True
def test_provider_error_message_does_not_stringify_sensitive_error_object():
    payload = b'{"error":{"api_key":"secret-value","details":{"token":"hidden"}}}'
    message = _error_message(payload, None)
    assert "secret-value" not in message and "hidden" not in message
    assert message == "provider returned an error"
