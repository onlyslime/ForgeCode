"""Provider construction kept at one auditable boundary.

All supported providers currently use the OpenAI-compatible wire contract;
custom gateways can therefore be selected by profile/base URL without a
second execution loop. Provider names remain explicit for diagnostics.
"""
from __future__ import annotations

import json
from typing import Any

from .openai_compatible import OpenAICompatibleProvider, UrllibTransport
from ..config import SUPPORTED_PROVIDERS


def _json_result(result: Any) -> tuple[int, bytes, dict[str, str]]:
    if not isinstance(result, tuple) or len(result) not in (2, 3):
        raise ValueError("transport returned invalid tuple")
    if isinstance(result[0], bool) or not isinstance(result[0], int):
        raise ValueError("transport returned invalid HTTP status")
    try:
        status = result[0]
        if not isinstance(result[1], (bytes, bytearray)):
            raise TypeError("body must be bytes")
        body = bytes(result[1])
        headers = dict(result[2]) if len(result) == 3 else {}
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("transport returned invalid result fields") from exc
    if status < 100 or status > 599:
        raise ValueError("transport returned invalid HTTP status")
    if len(body) > 4_000_000:
        raise ValueError("transport response body exceeds size limit")
    if not isinstance(headers, dict):
        raise ValueError("transport returned invalid headers")
    if len(headers) > 128 or any(not isinstance(key, str) or not isinstance(value, str) or len(key) > 256 or len(value) > 8_000 or any(ord(ch) < 32 for ch in key + value) for key, value in headers.items()):
        raise ValueError("transport returned invalid headers")
    return status, body, headers


class _ProtocolTransport:
    """Translate provider-specific JSON at the transport boundary."""
    def __init__(self, delegate: Any, provider: str, api_key: str):
        self.delegate, self.provider, self.api_key = delegate, provider, api_key

    def _request(self, url: str, headers: dict[str, str], body: bytes) -> tuple[str, dict[str, str], bytes]:
        if not isinstance(url, str) or not url or len(url) > 2_048 or any(ord(ch) < 32 for ch in url) or not url.lower().startswith(("http://", "https://")):
            raise ValueError("provider request URL is invalid")
        if not isinstance(headers, dict) or len(headers) > 128 or any(not isinstance(key, str) or not isinstance(value, str) or not key or len(key) > 256 or len(value) > 8_000 or any(ord(ch) < 32 for ch in key + value) for key, value in headers.items()):
            raise ValueError("provider request headers are invalid")
        if not isinstance(body, (bytes, bytearray)):
            raise ValueError("provider request body must be bytes")
        if len(body) > 4_000_000:
            raise ValueError("provider request body exceeds size limit")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("provider request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("provider request body must be an object")
        raw_messages = payload.get("messages", [])
        if not isinstance(raw_messages, list) or any(not isinstance(message, dict) for message in raw_messages):
            raise ValueError("provider messages must be objects")
        if self.provider == "anthropic":
            messages = payload.pop("messages", [])
            if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
                raise ValueError("provider messages must be objects")
            payload["max_tokens"] = payload.get("max_tokens", 4096)
            anthropic_messages = []
            for message in messages:
                role = message.get("role")
                if role == "tool":
                    anthropic_messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""), "content": str(message.get("content", ""))}]})
                    continue
                content: list[dict[str, Any]] = []
                if message.get("content"):
                    content.append({"type": "text", "text": str(message.get("content", ""))})
                for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
                    function = call.get("function", {}) if isinstance(call, dict) else {}
                    if isinstance(function, dict) and function.get("name"):
                        try:
                            arguments = json.loads(function.get("arguments", "{}"))
                        except (TypeError, ValueError):
                            arguments = {}
                        content.append({"type": "tool_use", "id": str(call.get("id", "")), "name": function["name"], "input": arguments})
                anthropic_messages.append({"role": "assistant" if role == "assistant" else "user", "content": content or str(message.get("content", ""))})
            payload["messages"] = anthropic_messages
            tools = payload.get("tools", [])
            if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
                raise ValueError("provider tool schemas must be objects")
            converted_tools = []
            for tool in tools:
                function = tool.get("function", {})
                if not isinstance(function, dict):
                    raise ValueError("provider tool function must be an object")
                converted_tools.append({"name": function.get("name"), "description": function.get("description", ""), "input_schema": function.get("parameters", {})})
            payload["tools"] = converted_tools
            headers = {k: v for k, v in headers.items() if k.lower() != "authorization"} | {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
            url = url.rsplit("/chat/completions", 1)[0] + "/messages"
        elif self.provider == "google":
            messages = payload.pop("messages", [])
            if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
                raise ValueError("provider messages must be objects")
            model = payload.get("model")
            raw_tools = payload.pop("tools", [])
            if not isinstance(raw_tools, list) or any(not isinstance(tool, dict) for tool in raw_tools):
                raise ValueError("provider tool schemas must be objects")
            declarations = []
            for tool in raw_tools:
                function = tool.get("function", {}) if isinstance(tool, dict) else {}
                if not isinstance(function, dict):
                    raise ValueError("provider tool function must be an object")
                if function.get("name"):
                    declarations.append({"name": function["name"], "description": str(function.get("description", "")), "parameters": function.get("parameters", {"type": "object"})})
            google_contents = []
            call_names: dict[str, str] = {}
            for message in messages:
                role = message.get("role")
                if role == "tool":
                    call_id = str(message.get("tool_call_id", ""))
                    google_contents.append({"role": "user", "parts": [{"functionResponse": {"name": call_names.get(call_id, call_id), "response": {"content": str(message.get("content", ""))}}}]})
                    continue
                parts: list[dict[str, Any]] = []
                if message.get("content"):
                    parts.append({"text": str(message.get("content", ""))})
                for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
                    function = call.get("function", {}) if isinstance(call, dict) else {}
                    if isinstance(function, dict) and function.get("name"):
                        call_id = str(call.get("id", ""))
                        call_names[call_id] = function["name"]
                        try:
                            arguments = json.loads(function.get("arguments", "{}"))
                        except (TypeError, ValueError):
                            arguments = {}
                        parts.append({"functionCall": {"name": function["name"], "args": arguments}})
                google_contents.append({"role": "user" if role == "user" else "model", "parts": parts or [{"text": ""}]})
            payload = {"contents": google_contents}
            if isinstance(model, str) and model:
                # Preserve the selected model for native gateways and
                # transparent proxies that route from the JSON body.
                payload["model"] = model
            if declarations:
                payload["tools"] = [{"functionDeclarations": declarations}]
            url = url.rsplit("/chat/completions", 1)[0] + ":generateContent"
            headers = {"Content-Type": "application/json"}
        elif self.provider == "ollama":
            url = url.rsplit("/chat/completions", 1)[0].rsplit("/v1", 1)[0] + "/api/chat"
            headers = {"Content-Type": "application/json"}
        return url, headers, json.dumps(payload, ensure_ascii=False).encode()

    def _response(self, body: bytes) -> bytes:
        if not isinstance(body, (bytes, bytearray)):
            raise ValueError("provider response body must be bytes")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("provider response body must be valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("provider response body must be an object")
        if self.provider == "anthropic":
            blocks = data.get("content", [])
            if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
                raise ValueError("anthropic response content must be a list of objects")
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            if any(not isinstance(part, str) for part in text_parts):
                raise ValueError("anthropic response text blocks must be strings")
            text = "".join(text_parts)
            calls = [{"id": b.get("id", "call"), "type": "function", "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input", {}))}} for b in blocks if b.get("type") == "tool_use"]
            data = {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": calls}, "finish_reason": "tool_calls" if calls else "stop"}], "usage": data.get("usage", {})}
        elif self.provider == "google":
            candidates = data.get("candidates", [{}])
            if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
                raise ValueError("google response candidates must contain an object")
            candidate_content = candidates[0].get("content", {})
            if not isinstance(candidate_content, dict):
                raise ValueError("google response candidate content must be an object")
            parts = candidate_content.get("parts", [])
            if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
                raise ValueError("google response parts must be objects")
            text_parts = [p.get("text", "") for p in parts if "text" in p]
            if any(not isinstance(part, str) for part in text_parts):
                raise ValueError("google response text parts must be strings")
            text = "".join(text_parts)
            calls = []
            for index, part in enumerate(parts):
                function = part.get("functionCall") if isinstance(part, dict) else None
                if isinstance(function, dict) and function.get("name"):
                    calls.append({"id": str(function.get("id") or f"call-{index}"), "type": "function", "function": {"name": function["name"], "arguments": json.dumps(function.get("args", {}), ensure_ascii=False)}})
            data = {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": calls}, "finish_reason": "tool_calls" if calls else "stop"}], "usage": data.get("usageMetadata", {})}
        elif self.provider == "ollama":
            msg = data.get("message", {})
            if not isinstance(msg, dict):
                raise ValueError("ollama response message must be an object")
            calls = []
            raw_calls = msg.get("tool_calls", [])
            if not isinstance(raw_calls, list) or any(not isinstance(call, dict) for call in raw_calls):
                raise ValueError("ollama response tool_calls must be objects")
            for index, call in enumerate(raw_calls):
                function = call.get("function", {}) if isinstance(call, dict) else {}
                if isinstance(function, dict) and function.get("name"):
                    calls.append({"id": str(call.get("id") or f"call-{index}"), "type": "function", "function": {"name": function["name"], "arguments": json.dumps(function.get("arguments", function.get("parameters", {})), ensure_ascii=False)}})
            content = msg.get("content", "")
            if not isinstance(content, str):
                raise ValueError("ollama response message content must be a string")
            data = {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": calls}, "finish_reason": "tool_calls" if calls else "stop"}], "usage": {}}
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
            anthropic_tools: dict[int, int] = {}
            next_tool_index = 0
            google_tools: dict[str, int] = {}
            google_tool_ids: dict[str, str] = {}
            google_next_tool_index = 0
            emitted_finish = False

            def frame(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
                choice = {"index": 0, "delta": delta, "finish_reason": finish_reason}
                return b"data: " + json.dumps({"choices": [choice]}, ensure_ascii=False).encode() + b"\n\n"

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
                    text = ""; done = False; finish_reason: str | None = None
                    if self.provider == "anthropic":
                        event_type = item.get("type")
                        if event_type == "content_block_delta":
                            block = item.get("index")
                            delta = item.get("delta", {})
                            if isinstance(delta, dict) and (delta.get("type") == "text_delta" or "text" in delta):
                                text = str(delta.get("text", ""))
                            elif isinstance(block, int) and block in anthropic_tools and isinstance(delta, dict) and delta.get("type") == "input_json_delta":
                                yield frame({"tool_calls": [{"index": anthropic_tools[block], "function": {"arguments": str(delta.get("partial_json", ""))}}]})
                        elif event_type == "content_block_start":
                            block = item.get("index")
                            content = item.get("content_block", {})
                            if isinstance(block, int) and isinstance(content, dict) and content.get("type") == "tool_use":
                                anthropic_tools[block] = next_tool_index
                                next_tool_index += 1
                                yield frame({"tool_calls": [{"index": anthropic_tools[block], "id": str(content.get("id", "")), "function": {"name": str(content.get("name", "")), "arguments": ""}}]})
                        elif event_type == "message_delta":
                            stop_reason = item.get("delta", {}).get("stop_reason") if isinstance(item.get("delta"), dict) else None
                            if stop_reason in {"tool_use", "end_turn", "max_tokens"}:
                                finish_reason = "tool_calls" if stop_reason == "tool_use" else ("length" if stop_reason == "max_tokens" else "stop")
                        done = event_type == "message_stop"
                    elif self.provider == "google":
                        parts = item.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
                        for part in parts:
                            function = part.get("functionCall") if isinstance(part, dict) else None
                            if isinstance(function, dict) and function.get("name"):
                                name = str(function["name"])
                                raw_args = function.get("args", {})
                                if not isinstance(raw_args, dict):
                                    raise ValueError("Google functionCall args must be an object")
                                if name not in google_tools:
                                    google_tools[name] = google_next_tool_index
                                    google_tool_ids[name] = str(function.get("id") or f"call-{google_next_tool_index}")
                                    google_next_tool_index += 1
                                    yield frame({"tool_calls": [{"index": google_tools[name], "id": google_tool_ids[name], "function": {"name": name, "arguments": json.dumps(raw_args, ensure_ascii=False)}}]})
                                elif raw_args:
                                    # Gemini normally emits one complete
                                    # functionCall object. A second frame for
                                    # the same call cannot be represented as a
                                    # safe JSON delta; reject it instead of
                                    # concatenating two objects into invalid
                                    # arguments that might reach a tool.
                                    raise ValueError("Google functionCall was emitted in multiple argument frames")
                        done = bool(item.get("candidates", [{}])[0].get("finishReason"))
                    else:
                        message = item.get("message", {})
                        text = str(message.get("content", ""))
                        for index, call in enumerate(message.get("tool_calls", []) if isinstance(message, dict) else []):
                            function = call.get("function", {}) if isinstance(call, dict) else {}
                            if isinstance(function, dict) and function.get("name"):
                                yield frame({"tool_calls": [{"index": index, "id": str(call.get("id") or f"call-{index}"), "function": {"name": function["name"], "arguments": json.dumps(function.get("arguments", function.get("parameters", {})), ensure_ascii=False)}}]})
                        done = bool(item.get("done"))
                    if text:
                        yield frame({"content": text})
                    if finish_reason is not None:
                        yield frame({}, finish_reason)
                        emitted_finish = True
                    if done:
                        if not emitted_finish:
                            yield frame({}, "tool_calls" if (anthropic_tools or google_tools) else "stop")
                        yield b"data: [DONE]\n\n"
        return status, events(), response_headers


class AnthropicProvider(OpenAICompatibleProvider):
    """Anthropic-compatible gateway adapter (OpenAI translation at boundary)."""
    provider_name = "anthropic"

    def __init__(self, **kwargs: Any):
        # The production provider creates its own UrllibTransport when none is
        # supplied. Wrap that default too; otherwise only injected test
        # transports receive Anthropic's /messages wire translation.
        transport = kwargs.get("transport") or UrllibTransport()
        kwargs["transport"] = _ProtocolTransport(transport, "anthropic", kwargs.get("api_key", ""))
        super().__init__(**kwargs)


class GoogleProvider(OpenAICompatibleProvider):
    provider_name = "google"

    def __init__(self, **kwargs: Any):
        transport = kwargs.get("transport") or UrllibTransport()
        kwargs["transport"] = _ProtocolTransport(transport, "google", kwargs.get("api_key", ""))
        super().__init__(**kwargs)


class OllamaProvider(OpenAICompatibleProvider):
    provider_name = "ollama"

    def __init__(self, **kwargs: Any):
        if not kwargs.get("api_key"):
            kwargs["api_key"] = "local"
        transport = kwargs.get("transport") or UrllibTransport()
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
