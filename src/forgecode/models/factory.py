"""Provider construction kept at one auditable boundary.

All supported providers currently use the OpenAI-compatible wire contract;
custom gateways can therefore be selected by profile/base URL without a
second execution loop. Provider names remain explicit for diagnostics.
"""
from __future__ import annotations

import json
from typing import Any

from .openai_compatible import OpenAICompatibleProvider
from ..config import SUPPORTED_PROVIDERS


def _json_result(result: Any) -> tuple[int, bytes, dict[str, str]]:
    if not isinstance(result, tuple) or len(result) not in (2, 3):
        raise ValueError("transport returned invalid tuple")
    return int(result[0]), bytes(result[1]), dict(result[2]) if len(result) == 3 else {}


class _ProtocolTransport:
    """Translate provider-specific JSON at the transport boundary."""
    def __init__(self, delegate: Any, provider: str, api_key: str):
        self.delegate, self.provider, self.api_key = delegate, provider, api_key

    def _request(self, url: str, headers: dict[str, str], body: bytes) -> tuple[str, dict[str, str], bytes]:
        payload = json.loads(body.decode("utf-8"))
        if self.provider == "anthropic":
            messages = payload.pop("messages", [])
            payload["max_tokens"] = payload.get("max_tokens", 4096)
            payload["messages"] = [{"role": m.get("role"), "content": m.get("content", "")} for m in messages]
            tools = payload.get("tools", [])
            payload["tools"] = [{"name": t.get("function", {}).get("name"), "description": t.get("function", {}).get("description", ""), "input_schema": t.get("function", {}).get("parameters", {})} for t in tools]
            headers = {k: v for k, v in headers.items() if k.lower() != "authorization"} | {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
            url = url.rsplit("/chat/completions", 1)[0] + "/messages"
        elif self.provider == "google":
            messages = payload.pop("messages", [])
            payload = {"contents": [{"role": "user" if m.get("role") == "user" else "model", "parts": [{"text": str(m.get("content", ""))}]} for m in messages], "tools": payload.get("tools", [])}
            url = url.rsplit("/chat/completions", 1)[0] + ":generateContent"
            headers = {"Content-Type": "application/json"}
        elif self.provider == "ollama":
            url = url.rsplit("/chat/completions", 1)[0].rsplit("/v1", 1)[0] + "/api/chat"
            headers = {"Content-Type": "application/json"}
        return url, headers, json.dumps(payload, ensure_ascii=False).encode()

    def _response(self, body: bytes) -> bytes:
        data = json.loads(body.decode("utf-8"))
        if self.provider == "anthropic":
            blocks = data.get("content", [])
            text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
            calls = [{"id": b.get("id", "call"), "type": "function", "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input", {}))}} for b in blocks if b.get("type") == "tool_use"]
            data = {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": calls}, "finish_reason": "tool_calls" if calls else "stop"}], "usage": data.get("usage", {})}
        elif self.provider == "google":
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(str(p.get("text", "")) for p in parts)
            data = {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": data.get("usageMetadata", {})}
        elif self.provider == "ollama":
            msg = data.get("message", {})
            data = {"choices": [{"message": {"role": "assistant", "content": msg.get("content", "")}, "finish_reason": "stop"}], "usage": {}}
        return json.dumps(data, ensure_ascii=False).encode()

    def post_json(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        url, headers, body = self._request(url, headers, body)
        result = self.delegate.post_json(url, headers, body, timeout)
        status, response, response_headers = _json_result(result)
        return status, self._response(response) if 200 <= status < 300 else response, response_headers

    def post_stream(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        translated_url, translated_headers, translated_body = self._request(url, headers, body)
        translated_headers = {**translated_headers, "Accept": "text/event-stream"}
        raw_result = self.delegate.post_stream(translated_url, translated_headers, translated_body, timeout)
        if not isinstance(raw_result, tuple) or len(raw_result) not in (2, 3):
            raise ValueError("stream transport returned invalid tuple")
        status, chunks = raw_result[0], raw_result[1]
        response_headers = dict(raw_result[2]) if len(raw_result) == 3 else {}

        def events():
            for raw in chunks:
                if not isinstance(raw, (bytes, bytearray)):
                    raise ValueError("provider stream yielded non-byte data")
                for line in bytes(raw).splitlines():
                    if not line.strip():
                        continue
                    payload = line[5:].lstrip() if line.startswith(b"data:") else line
                    if payload == b"[DONE]":
                        yield b"data: [DONE]\n\n"; continue
                    try:
                        item = json.loads(payload.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    text = ""; done = False
                    if self.provider == "anthropic":
                        event_type = item.get("type")
                        if event_type == "content_block_delta":
                            text = str(item.get("delta", {}).get("text", ""))
                        done = event_type == "message_stop"
                    elif self.provider == "google":
                        parts = item.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        text = "".join(str(part.get("text", "")) for part in parts)
                        done = bool(item.get("candidates", [{}])[0].get("finishReason"))
                    else:
                        message = item.get("message", {})
                        text = str(message.get("content", ""))
                        done = bool(item.get("done"))
                    if text:
                        yield (b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}).encode() + b"\n\n")
                    if done:
                        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        return status, events(), response_headers


class AnthropicProvider(OpenAICompatibleProvider):
    """Anthropic-compatible gateway adapter (OpenAI translation at boundary)."""
    provider_name = "anthropic"

    def __init__(self, **kwargs: Any):
        transport = kwargs.get("transport")
        if transport is not None:
            kwargs["transport"] = _ProtocolTransport(transport, "anthropic", kwargs.get("api_key", ""))
        super().__init__(**kwargs)


class GoogleProvider(OpenAICompatibleProvider):
    provider_name = "google"

    def __init__(self, **kwargs: Any):
        transport = kwargs.get("transport")
        if transport is not None:
            kwargs["transport"] = _ProtocolTransport(transport, "google", kwargs.get("api_key", ""))
        super().__init__(**kwargs)


class OllamaProvider(OpenAICompatibleProvider):
    provider_name = "ollama"

    def __init__(self, **kwargs: Any):
        if not kwargs.get("api_key"):
            kwargs["api_key"] = "local"
        transport = kwargs.get("transport")
        if transport is not None:
            kwargs["transport"] = _ProtocolTransport(transport, "ollama", kwargs.get("api_key", "local"))
        super().__init__(**kwargs)


def create_provider(*, provider: str, api_key: str, base_url: str, model: str, streaming: bool = False, stream_required: bool = False, timeout: float = 60.0):
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    cls = {"openai-compatible": OpenAICompatibleProvider, "anthropic": AnthropicProvider, "google": GoogleProvider, "ollama": OllamaProvider}.get(provider, OpenAICompatibleProvider)
    # Ollama is normally local and does not require a credential; the
    # provider-neutral adapter still needs a non-empty auth marker internally.
    return cls(api_key=(api_key or "local") if provider == "ollama" else api_key, base_url=base_url, model=model, streaming=streaming, stream_required=stream_required, timeout=timeout)


__all__ = ["AnthropicProvider", "GoogleProvider", "OllamaProvider", "create_provider"]
