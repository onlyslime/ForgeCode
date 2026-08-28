import asyncio
import json

from forgecode.models import AnthropicProvider, GoogleProvider, Message, OllamaProvider


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
