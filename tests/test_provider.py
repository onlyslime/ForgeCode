import asyncio
import json

import pytest

from forgecode.models import Message, ModelResponse, OpenAICompatibleProvider, ProviderError, ToolCall, is_valid_response, parse_chat_completion, assemble_chat_stream
from forgecode.models.openai_compatible import _tool_schema_to_payload
from forgecode.models.factory import _ProtocolTransport, _json_result


class RecordingTransport:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {}
        self.calls = []

    def post_json(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body), timeout))
        return self.status, json.dumps(self.payload).encode()


def test_provider_builds_openai_request_and_parses_multiple_calls():
    transport = RecordingTransport(
        payload={
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "inspect",
                        "tool_calls": [
                            {"id": "call-a", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}},
                            {"id": "call-b", "type": "function", "function": {"name": "list_files", "arguments": "{}"}},
                        ],
                    },
                }
            ]
        }
    )
    provider = OpenAICompatibleProvider(api_key="key-123", base_url="https://example.test/v1", model="demo", transport=transport)
    response = asyncio.run(provider.complete([Message("user", "hello")], [{"name": "read_file", "description": "read", "parameters": {"type": "object"}}]))

    assert response.finish_reason == "tool_calls"
    assert [call.id for call in response.message.tool_calls] == ["call-a", "call-b"]
    assert response.message.tool_calls[0].arguments == {"path": "a.txt"}
    url, headers, body, timeout = transport.calls[0]
    assert url == "https://example.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer key-123"
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "read_file"


def test_provider_http_error_redacts_key():
    transport = RecordingTransport(status=401, payload={"error": {"message": "bad key-123"}})
    provider = OpenAICompatibleProvider(api_key="key-123", base_url="https://example.test/v1", model="demo", transport=transport)
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(provider.complete([], []))
    assert exc_info.value.category == "http_error"
    assert "key-123" not in str(exc_info.value)
    assert "REDACTED" in str(exc_info.value)


def test_provider_error_redacts_bearer_and_named_secret_text():
    transport = RecordingTransport(status=500, payload={"error": {"message": "Authorization: Bearer abc123 token=inline-secret"}})
    provider = OpenAICompatibleProvider(api_key="key-123", base_url="https://example.test/v1", model="demo", transport=transport)
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(provider.complete([], []))
    message = str(exc_info.value)
    assert "abc123" not in message
    assert "inline-secret" not in message


def test_provider_rejects_malformed_tool_arguments():
    with pytest.raises(ProviderError, match="invalid JSON arguments"):
        parse_chat_completion({"choices": [{"message": {"content": None, "tool_calls": [{"id": "x", "function": {"name": "read_file", "arguments": "{"}}]}}]})


def test_provider_requires_configuration():
    with pytest.raises(ProviderError, match="API_KEY"):
        OpenAICompatibleProvider(api_key="", base_url="https://example.test/v1", model="demo")


def test_provider_capabilities_advertise_transport_modes():
    plain = OpenAICompatibleProvider(api_key="fake", base_url="https://example.test/v1", model="demo", streaming=False)
    streamed = OpenAICompatibleProvider(api_key="fake", base_url="https://example.test/v1", model="demo", streaming=True)
    assert plain.capabilities.to_dict()["transports"] == ("json",)
    assert streamed.capabilities.to_dict()["transports"] == ("json", "sse")


def test_model_capabilities_reject_unbounded_or_duplicate_values():
    from forgecode.models import ModelCapabilities
    with pytest.raises(ValueError, match="max_input_chars"):
        ModelCapabilities(max_input_chars=0)
    with pytest.raises(ValueError, match="transports"):
        ModelCapabilities(transports=("json", "json"))


def test_provider_rejects_non_string_finish_reason():
    with pytest.raises(ProviderError, match="finish_reason"):
        parse_chat_completion({"choices": [{"finish_reason": 1, "message": {"content": "ok"}}]})


def test_provider_rejects_non_object_payload():
    with pytest.raises(ProviderError, match="JSON object"):
        parse_chat_completion([])


def test_stream_assembly_rejects_non_object_event():
    with pytest.raises(ProviderError, match="event must be a JSON object"):
        assemble_chat_stream([None])


def test_stream_assembly_rejects_negative_tool_index():
    with pytest.raises(ProviderError, match="integer index"):
        assemble_chat_stream([{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": -1}]}}]}])


def test_provider_rejects_control_character_tool_call_id():
    with pytest.raises(ProviderError, match="no id"):
        parse_chat_completion({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{"id": "bad\nid", "function": {"name": "read_file", "arguments": "{}"}}]}}]})


def test_provider_rejects_control_character_usage_key():
    with pytest.raises(ProviderError, match="invalid field name"):
        parse_chat_completion({"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {"bad\nkey": 1}})


def test_provider_rejects_non_object_tool_schema():
    with pytest.raises(ProviderError, match="tool schema must be"):
        _tool_schema_to_payload(None)


def test_provider_rejects_invalid_wrapped_tool_name():
    with pytest.raises(ProviderError, match="function name is invalid"):
        _tool_schema_to_payload({"type": "function", "function": {"name": "bad\tname"}})


def test_provider_rejects_invalid_wrapped_tool_fields():
    with pytest.raises(ProviderError, match="function fields are invalid"):
        _tool_schema_to_payload({"type": "function", "function": {"name": "ok", "parameters": []}})
    with pytest.raises(ProviderError, match="function fields are invalid"):
        _tool_schema_to_payload({"type": "function", "function": {"name": "ok", "description": "bad\ntext"}})


def test_anthropic_transport_rejects_non_object_tool_schema():
    transport = _ProtocolTransport(object(), "anthropic", "secret")
    with pytest.raises(ValueError, match="tool schemas must be objects"):
        transport._request("https://example/v1/chat/completions", {}, json.dumps({"messages": [], "tools": [None]}).encode())


def test_provider_transport_rejects_non_object_request():
    transport = _ProtocolTransport(object(), "anthropic", "secret")
    with pytest.raises(ValueError, match="request body must be an object"):
        transport._request("https://example/v1/chat/completions", {}, b"[]")


def test_provider_transport_rejects_non_bytes_request_body():
    transport = _ProtocolTransport(object(), "anthropic", "secret")
    with pytest.raises(ValueError, match="request body must be bytes"):
        transport._request("https://example/v1/chat/completions", {}, "{}")


def test_provider_transport_rejects_invalid_request_headers():
    transport = _ProtocolTransport(object(), "anthropic", "secret")
    with pytest.raises(ValueError, match="request headers are invalid"):
        transport._request("https://example/v1/chat/completions", {"X-Test": 1}, b"{}")


def test_google_transport_rejects_non_object_tool_schema():
    transport = _ProtocolTransport(object(), "google", "secret")
    with pytest.raises(ValueError, match="tool schemas must be objects"):
        transport._request("https://example/v1/chat/completions", {}, json.dumps({"messages": [], "tools": [None]}).encode())


def test_provider_transport_rejects_non_object_response():
    transport = _ProtocolTransport(object(), "anthropic", "secret")
    with pytest.raises(ValueError, match="response body must be an object"):
        transport._response(b"[]")


def test_anthropic_transport_rejects_invalid_content_blocks():
    transport = _ProtocolTransport(object(), "anthropic", "secret")
    with pytest.raises(ValueError, match="content must be a list"):
        transport._response(json.dumps({"content": {"type": "text"}}).encode())


def test_google_transport_rejects_invalid_candidates():
    transport = _ProtocolTransport(object(), "google", "secret")
    with pytest.raises(ValueError, match="candidates must contain"):
        transport._response(json.dumps({"candidates": []}).encode())


def test_ollama_transport_rejects_invalid_message():
    transport = _ProtocolTransport(object(), "ollama", "secret")
    with pytest.raises(ValueError, match="message must be an object"):
        transport._response(json.dumps({"message": []}).encode())


def test_transport_result_rejects_invalid_status():
    with pytest.raises(ValueError, match="invalid HTTP status"):
        _json_result((True, b"{}"))


def test_transport_result_rejects_non_string_headers():
    with pytest.raises(ValueError, match="invalid headers"):
        _json_result((200, b"{}", {"X-Test": 1}))


def test_provider_neutral_response_validation_rejects_nonfinite_usage_and_bad_finish_reason():
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), finish_reason="made_up"))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), usage={"total_tokens": float("nan")}))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), usage={"total_tokens": -1}))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), usage={"total_tokens": 10 ** 5_000}))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), usage={"total_tokens": 1e308}))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), usage={1: 2}))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok", tool_calls=(ToolCall("x", "read_file", {"value": 10 ** 1_000}),))))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok", tool_calls=(ToolCall("x", "read_file", {"value": 10 ** 5_000}),))))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok", tool_calls=(ToolCall("x", "read_file", {}),)), finish_reason="stop"))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok", tool_calls=(ToolCall("bad\nid", "read_file", {}),)), finish_reason="tool_calls"))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok", tool_call_id="bad\nid")))
