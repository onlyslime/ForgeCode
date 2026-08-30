import asyncio
import json

import pytest
from forgecode.models import AnthropicProvider, GoogleProvider, Message, OllamaProvider, ProviderError, ToolCall


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body)))
        return 200, json.dumps(self.response).encode(), {}

    def post_stream(self, url, headers, body, timeout):
        return 200, [b'data: ' + json.dumps(self.response).encode() + b'\n\n'], {}


def test_anthropic_protocol_translation():
    transport = Transport({"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1}})
    provider = AnthropicProvider(api_key="secret", base_url="https://a.test/v1", model="m", transport=transport)
    result = asyncio.run(provider.complete([Message("user", "hi")], []))
    assert result.message.content == "ok"
    assert transport.calls[0][0].endswith("/messages")
    assert transport.calls[0][1]["x-api-key"] == "secret"


def test_google_and_ollama_protocol_translation():
    google_t = Transport({"candidates": [{"content": {"parts": [{"text": "g"}]}}]})
    google = GoogleProvider(api_key="k", base_url="https://g.test/v1", model="m", transport=google_t)
    assert asyncio.run(google.complete([Message("user", "hi")], [])).message.content == "g"
    assert google_t.calls[0][0].endswith(":generateContent")
    ollama_t = Transport({"message": {"role": "assistant", "content": "o"}})
    ollama = OllamaProvider(api_key="", base_url="http://localhost:11434/v1", model="m", transport=ollama_t)
    assert asyncio.run(ollama.complete([Message("user", "hi")], [])).message.content == "o"
    assert ollama_t.calls[0][0].endswith("/api/chat")


def test_google_tool_schema_and_function_call_translation():
    google_t = Transport({"candidates": [{"content": {"parts": [{"functionCall": {"name": "read_file", "args": {"path": "README.md"}}}]}}]})
    google = GoogleProvider(api_key="k", base_url="https://g.test/v1", model="m", transport=google_t)
    tools = [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    result = asyncio.run(google.complete([Message("user", "read")], tools))
    assert google_t.calls[0][1]["Content-Type"] == "application/json"
    assert google_t.calls[0][2]["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
    assert result.finish_reason == "tool_calls"
    assert result.message.tool_calls[0].name == "read_file"
    assert result.message.tool_calls[0].arguments == {"path": "README.md"}


def test_google_tool_result_history_uses_function_response():
    google_t = Transport({"candidates": [{"content": {"parts": [{"text": "done"}]}}]})
    google = GoogleProvider(api_key="k", base_url="https://g.test/v1", model="m", transport=google_t)
    history = [
        Message("user", "read"),
        Message("assistant", tool_calls=(ToolCall("call-1", "read_file", {"path": "README.md"}),)),
        Message("tool", "contents", tool_call_id="call-1"),
    ]
    asyncio.run(google.complete(history, []))
    contents = google_t.calls[0][2]["contents"]
    assert contents[-1]["parts"][0]["functionResponse"]["name"] == "read_file"


def test_google_stream_function_call_is_normalized():
    class StreamTransport(Transport):
        def post_stream(self, url, headers, body, timeout):
            frames = [
                b'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"read_file","args":{"path":"README.md"}}}]},"finishReason":"STOP"}]}\n\n',
            ]
            return 200, frames, {}
    provider = GoogleProvider(api_key="k", base_url="https://g.test/v1", model="m", transport=StreamTransport({}), streaming=True)
    result = asyncio.run(provider.complete([Message("user", "read")], []))
    assert result.finish_reason == "tool_calls"
    assert result.message.tool_calls[0].name == "read_file"
    assert result.message.tool_calls[0].arguments == {"path": "README.md"}


def test_google_stream_rejects_split_function_call_arguments():
    class SplitTransport(Transport):
        def post_stream(self, url, headers, body, timeout):
            frames = [
                b'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"read_file","args":{"path":"README.md"}}}]}}]}\n\n',
                b'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"read_file","args":{"extra":true}}}]},"finishReason":"STOP"}]}\n\n',
            ]
            return 200, frames, {}
    provider = GoogleProvider(api_key="k", base_url="https://g.test/v1", model="m", transport=SplitTransport({}), streaming=True, max_retries=0)
    with pytest.raises(ProviderError, match="multiple argument frames"):
        asyncio.run(provider.complete([Message("user", "read")], []))


def test_ollama_tool_call_translation():
    transport = Transport({"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "function": {"name": "read_file", "arguments": {"path": "README.md"}}}]}})
    provider = OllamaProvider(api_key="", base_url="http://localhost:11434/v1", model="m", transport=transport)
    result = asyncio.run(provider.complete([Message("user", "read")], []))
    assert result.finish_reason == "tool_calls"
    assert result.message.tool_calls[0].name == "read_file"
    assert result.message.tool_calls[0].arguments == {"path": "README.md"}


def test_anthropic_stream_is_normalized_to_openai_sse():
    class StreamTransport(Transport):
        def post_stream(self, url, headers, body, timeout):
            frames = [
                b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n',
                b'data: {"type":"message_stop"}\n\n',
            ]
            return 200, frames, {}
    provider = AnthropicProvider(api_key="secret", base_url="https://a.test/v1", model="m", transport=StreamTransport({}), streaming=True)
    result = asyncio.run(provider.complete([Message("user", "hi")], []))
    assert result.message.content == "hi"


def test_anthropic_stream_tool_use_is_normalized_to_openai_tool_call():
    class ToolStreamTransport(Transport):
        def post_stream(self, url, headers, body, timeout):
            frames = [
                b'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call-1","name":"read_file"}}\n\n',
                b'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":\\"README.md\\"}"}}\n\n',
                b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}\n\n',
                b'data: {"type":"message_stop"}\n\n',
            ]
            return 200, frames, {}
    provider = AnthropicProvider(api_key="secret", base_url="https://a.test/v1", model="m", transport=ToolStreamTransport({}), streaming=True)
    result = asyncio.run(provider.complete([Message("user", "read")], []))
    assert result.finish_reason == "tool_calls"
    assert result.message.tool_calls[0].id == "call-1"
    assert result.message.tool_calls[0].name == "read_file"
    assert result.message.tool_calls[0].arguments == {"path": "README.md"}


def test_stream_interruption_has_stable_error_category():
    class InterruptedTransport(Transport):
        def post_stream(self, url, headers, body, timeout):
            def frames():
                yield b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
                raise OSError("connection reset")
            return 200, frames(), {}

    provider = AnthropicProvider(api_key="secret", base_url="https://a.test/v1", model="m", transport=InterruptedTransport({}), streaming=True, max_retries=0)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.complete([Message("user", "hi")], []))
    assert caught.value.category == "stream_error"
    assert caught.value.retryable is True
    assert provider.attempt_events[-1]["outcome"] == "error"
