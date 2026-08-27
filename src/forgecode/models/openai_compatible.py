"""Minimal OpenAI-compatible Chat Completions adapter.

The adapter deliberately owns only HTTP and response conversion. Agent state,
tool execution, approval, and retry decisions remain in ForgeCode itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import math
from typing import Any, Protocol, Sequence
from urllib import error, request

from ..security.redaction import redact_text
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
    return redact_text(text, (secret,) if secret else ())


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


def _parse_tool_calls(raw_calls: Any, *, max_calls: int = 64) -> tuple[ToolCall, ...]:
    if raw_calls is None:
        return ()
    if not isinstance(raw_calls, list):
        raise ProviderError("model response tool_calls must be a list", category="protocol_error")
    if len(raw_calls) > max_calls:
        raise ProviderError(f"model response contains more than {max_calls} tool calls", category="response_limit")
    calls: list[ToolCall] = []
    seen_ids: set[str] = set()
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            raise ProviderError(f"model response tool call {index} is not an object", category="protocol_error")
        call_id = raw_call.get("id")
        function = raw_call.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise ProviderError(f"model response tool call {index} has no id", category="protocol_error")
        if call_id in seen_ids:
            raise ProviderError(f"model response repeats tool call id {call_id}", category="protocol_error")
        seen_ids.add(call_id)
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
    role = raw_message.get("role", "assistant")
    if role != "assistant":
        raise ProviderError("model response message role must be assistant", category="protocol_error")
    content = raw_message.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ProviderError("model response content must be text or null", category="protocol_error")
    if len(content) > 200_000:
        raise ProviderError("model response content exceeded the configured size limit", category="response_limit")
    calls = _parse_tool_calls(raw_message.get("tool_calls"))
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise ProviderError("model response finish_reason must be text or null", category="protocol_error")
    usage = payload.get("usage", {})
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        raise ProviderError("model response usage must be an object", category="protocol_error")
    safe_usage = {str(key): value for key, value in list(usage.items())[:32] if isinstance(value, (int, float)) and not isinstance(value, bool)}
    return ModelResponse(Message(role="assistant", content=content, tool_calls=calls), finish_reason, safe_usage)


class OpenAICompatibleProvider:
    def __init__(self, *, api_key: str, base_url: str, model: str, transport: JsonTransport | None = None, timeout: float = 60.0, max_response_bytes: int = 4_000_000, max_request_bytes: int = 4_000_000, max_retries: int = 2, retry_base_delay: float = 0.25):
        if not api_key:
            raise ProviderError("FORGECODE_API_KEY is not configured", category="configuration_error")
        if not model:
            raise ProviderError("FORGECODE_MODEL is not configured", category="configuration_error")
        if not base_url:
            raise ProviderError("FORGECODE_BASE_URL is empty", category="configuration_error")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if isinstance(max_request_bytes, bool) or not isinstance(max_request_bytes, int) or max_request_bytes < 1:
            raise ValueError("max_request_bytes must be positive")
        if isinstance(max_retries, bool) or max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries must be between 0 and 5")
        if isinstance(retry_base_delay, bool) or not isinstance(retry_base_delay, (int, float)) or not math.isfinite(retry_base_delay) or retry_base_delay < 0 or retry_base_delay > 10:
            raise ValueError("retry_base_delay must be between 0 and 10 seconds")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_request_bytes = max_request_bytes
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_events: list[dict[str, Any]] = []

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
        if len(body) > self.max_request_bytes:
            raise ProviderError("model request exceeded the configured size limit", category="request_limit")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        self.retry_events = []
        for attempt in range(1, self.max_retries + 2):
            try:
                status, response_body = await asyncio.to_thread(self.transport.post_json, f"{self.base_url}/chat/completions", headers, body, self.timeout)
            except (TimeoutError, error.URLError, OSError) as exc:
                retryable = True
                if attempt <= self.max_retries:
                    await self._retry(attempt, "transport_error", str(exc))
                    continue
                raise ProviderError(_redact(f"model request failed: {exc}", self.api_key), category="transport_error", retryable=retryable, attempt=attempt) from exc
            except Exception as exc:
                raise ProviderError(_redact(f"model request failed: {type(exc).__name__}: {exc}", self.api_key), category="transport_error", retryable=False, attempt=attempt) from exc
            if not isinstance(response_body, (bytes, bytearray)):
                raise ProviderError("model transport returned a non-byte response", category="transport_error", attempt=attempt)
            if isinstance(status, bool) or not isinstance(status, int):
                raise ProviderError("model transport returned an invalid HTTP status", category="transport_error", attempt=attempt)
            retryable_status = status in {408, 429} or 500 <= status <= 599
            if retryable_status and attempt <= self.max_retries:
                await self._retry(attempt, f"http_{status}", f"HTTP {status}")
                continue
            break
        if not isinstance(response_body, (bytes, bytearray)):
            raise ProviderError("model transport returned a non-byte response", category="transport_error")
        if isinstance(status, bool) or not isinstance(status, int):
            raise ProviderError("model transport returned an invalid HTTP status", category="transport_error")
        if len(response_body) > self.max_response_bytes:
            raise ProviderError("model response exceeded the configured size limit", category="response_limit")
        if status < 200 or status >= 300:
            raise ProviderError(f"model returned HTTP {status}: {_error_message(response_body, self.api_key)}", category="http_error", retryable=status in {408, 429} or 500 <= status <= 599, status_code=status, attempt=attempt)
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("model returned malformed JSON", category="protocol_error") from exc
        if not isinstance(payload, dict):
            raise ProviderError("model response must be a JSON object", category="protocol_error")
        if payload.get("error"):
            safe_message = _redact(str(payload.get("error"))[:2_000], self.api_key)
            raise ProviderError(f"model returned an error object: {safe_message}", category="provider_error", attempt=attempt)
        return parse_chat_completion(payload)

    async def _retry(self, attempt: int, category: str, reason: str) -> None:
        # Jitter prevents synchronized clients while the cap keeps tests and
        # interactive use bounded. Do not retry local side effects here.
        delay = min(4.0, self.retry_base_delay * (2 ** (attempt - 1)))
        jitter = random.SystemRandom().uniform(0, delay * 0.25) if delay else 0.0
        wait = delay + jitter
        event = {"attempt": attempt, "next_attempt": attempt + 1, "category": category, "delay_seconds": round(wait, 3), "reason": _redact(reason, self.api_key)}
        self.retry_events.append(event)
        if wait:
            await asyncio.sleep(wait)
