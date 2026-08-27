import asyncio
import json

import pytest

from forgecode.models import Message, ModelResponse, OpenAICompatibleProvider, ProviderError, ToolCall, is_valid_response, parse_chat_completion


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


def test_provider_rejects_non_string_finish_reason():
    with pytest.raises(ProviderError, match="finish_reason"):
        parse_chat_completion({"choices": [{"finish_reason": 1, "message": {"content": "ok"}}]})


def test_provider_neutral_response_validation_rejects_nonfinite_usage_and_bad_finish_reason():
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), finish_reason="made_up"))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok"), usage={"total_tokens": float("nan")}))
    assert not is_valid_response(ModelResponse(Message("assistant", "ok", tool_calls=(ToolCall("x", "read_file", {}),)), finish_reason="stop"))
