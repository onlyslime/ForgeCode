"""Minimal OpenAI-compatible Chat Completions adapter.

The adapter deliberately owns only HTTP and response conversion. Agent state,
tool execution, approval, and retry decisions remain in ForgeCode itself.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Protocol, Sequence
from urllib import error, request

from .protocol import Message, ModelResponse, ProviderError, ToolCall


class JsonTransport(Protocol):
    def post_json(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        """Send a JSON POST and return status code plus response bytes."""


class UrllibTransport:
    def post_json(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        http_request = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                return response.status, response.read()
        except error.HTTPError as exc:
            return exc.code, exc.read()


def _redact(text: str, secret: str | None = None) -> str:
    value = text
    if secret:
        value = value.replace(secret, "[REDACTED]")
    value = value.replace("Bearer ", "Bearer [REDACTED]")
    return value


def _error_message(payload: bytes, secret: str | None) -> str:
    text = payload.decode("utf-8", errors="replace")[:2_000]
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            error_data = parsed.get("error")
            if isinstance(error_data, dict):
                text = str(error_data.get("message") or error_data.get("type") or error_data)
            elif error_data:
                text = str(error_data)
    except json.JSONDecodeError:
        pass
    return _redact(text, secret)


def _message_to_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
            }
            for call in message.tool_calls
        ]
    return payload


def _tool_schema_to_payload(schema: dict[str, Any]) -> dict[str, Any]:
    """Accept provider-neutral or already wrapped schemas and emit OpenAI shape."""
    if schema.get("type") == "function" and isinstance(schema.get("function"), dict):
        return schema
    if isinstance(schema.get("name"), str):
        return {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
            },
        }
    raise ProviderError("tool schema is missing a function name", category="protocol_error")


def _parse_tool_calls(raw_calls: Any) -> tuple[ToolCall, ...]:
    if raw_calls is None:
        return ()
    if not isinstance(raw_calls, list):
        raise ProviderError("model response tool_calls must be a list", category="protocol_error")
    calls: list[ToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            raise ProviderError(f"model response tool call {index} is not an object", category="protocol_error")
        call_id = raw_call.get("id")
        function = raw_call.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise ProviderError(f"model response tool call {index} has no id", category="protocol_error")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"]:
            raise ProviderError(f"model response tool call {index} has no function name", category="protocol_error")
        arguments = function.get("arguments", "{}")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ProviderError(f"tool call {call_id} has invalid JSON arguments", category="protocol_error") from exc
        if not isinstance(arguments, dict):
            raise ProviderError(f"tool call {call_id} arguments must be an object", category="protocol_error")
        calls.append(ToolCall(call_id, function["name"], arguments))
    return tuple(calls)


def parse_chat_completion(payload: dict[str, Any]) -> ModelResponse:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("model response has no choices", category="protocol_error")
    choice = choices[0]
    raw_message = choice.get("message")
    if not isinstance(raw_message, dict):
        raise ProviderError("model response has no message", category="protocol_error")
    content = raw_message.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ProviderError("model response content must be text or null", category="protocol_error")
    calls = _parse_tool_calls(raw_message.get("tool_calls"))
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ProviderError("model response finish_reason must be text or null", category="protocol_error")
    return ModelResponse(Message(role="assistant", content=content, tool_calls=calls), finish_reason)


class OpenAICompatibleProvider:
    def __init__(self, *, api_key: str, base_url: str, model: str, transport: JsonTransport | None = None, timeout: float = 60.0, max_response_bytes: int = 4_000_000):
        if not api_key:
            raise ProviderError("FORGECODE_API_KEY is not configured", category="configuration_error")
        if not model:
            raise ProviderError("FORGECODE_MODEL is not configured", category="configuration_error")
        if not base_url:
            raise ProviderError("FORGECODE_BASE_URL is empty", category="configuration_error")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    @classmethod
    def from_environment(cls, *, transport: JsonTransport | None = None) -> "OpenAICompatibleProvider":
        return cls(
            api_key=os.getenv("FORGECODE_API_KEY", ""),
            base_url=os.getenv("FORGECODE_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("FORGECODE_MODEL", ""),
            transport=transport,
        )

    async def complete(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]]) -> ModelResponse:
        try:
            tool_payloads = [_tool_schema_to_payload(schema) for schema in tools]
        except AttributeError as exc:
            raise ProviderError("tool schema must be an object", category="protocol_error") from exc
        body = json.dumps({"model": self.model, "messages": [_message_to_payload(message) for message in messages], "tools": tool_payloads}, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            status, response_body = await asyncio.to_thread(self.transport.post_json, f"{self.base_url}/chat/completions", headers, body, self.timeout)
        except (TimeoutError, error.URLError, OSError) as exc:
            raise ProviderError(_redact(f"model request failed: {exc}", self.api_key), category="transport_error") from exc
        except Exception as exc:
            raise ProviderError(_redact(f"model request failed: {type(exc).__name__}: {exc}", self.api_key), category="transport_error") from exc
        if not isinstance(response_body, (bytes, bytearray)):
            raise ProviderError("model transport returned a non-byte response", category="transport_error")
        if isinstance(status, bool) or not isinstance(status, int):
            raise ProviderError("model transport returned an invalid HTTP status", category="transport_error")
        if len(response_body) > self.max_response_bytes:
            raise ProviderError("model response exceeded the configured size limit", category="response_limit")
        if status < 200 or status >= 300:
            raise ProviderError(f"model returned HTTP {status}: {_error_message(response_body, self.api_key)}", category="http_error")
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("model returned malformed JSON", category="protocol_error") from exc
        if not isinstance(payload, dict):
            raise ProviderError("model response must be a JSON object", category="protocol_error")
        return parse_chat_completion(payload)
