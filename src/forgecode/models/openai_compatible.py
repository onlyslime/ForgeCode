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
import threading
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Protocol, Sequence
from urllib import error, request
from urllib.parse import urlsplit

from ..security.redaction import redact_text
from ..security.json import bounded_json_loads
from .protocol import CancellationToken, Message, ModelCapabilities, ModelResponse, ProviderContext, ProviderError, ToolCall


class _BoundedOperationTimeout(TimeoutError):
    """A timeout after which a blocking worker/iterator may still be alive."""

    unresolved = True


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _validate_json_value(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> None:
    """Reject non-standard/non-bounded values before tool dispatch."""
    if budget is None:
        budget = [100_000]
    if depth > 24:
        raise ProviderError("tool arguments exceeded nesting limit", category="response_limit")
    budget[0] -= 1
    if budget[0] < 0:
        raise ProviderError("tool arguments contain too many values", category="response_limit")
    if isinstance(value, float) and not math.isfinite(value):
        raise ProviderError("tool arguments contain a non-finite number", category="protocol_error")
    if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() >= 3_322:
        raise ProviderError("tool arguments contain an oversized integer", category="response_limit")
    if isinstance(value, str) and len(value) > 200_000:
        raise ProviderError("tool arguments contain an oversized string", category="response_limit")
    if isinstance(value, dict):
        if len(value) > 10_000:
            raise ProviderError("tool arguments contain too many object fields", category="response_limit")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProviderError("tool argument object keys must be strings", category="protocol_error")
            _validate_json_value(item, depth=depth + 1, budget=budget)
    elif isinstance(value, list):
        if len(value) > 10_000:
            raise ProviderError("tool arguments contain too many array values", category="response_limit")
        for item in value:
            _validate_json_value(item, depth=depth + 1, budget=budget)


class JsonTransport(Protocol):
    def post_json(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes] | tuple[int, bytes, dict[str, str]]:
        """Send a JSON POST and return status code plus response bytes."""

    def post_stream(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, Iterable[bytes]] | tuple[int, Iterable[bytes], dict[str, str]]:
        """Optional SSE transport. Implementations may omit this method."""


class UrllibTransport:
    def post_json(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        http_request = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                return response.status, response.read()
        except error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers.items()) if exc.headers else {}

    def post_stream(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, Iterable[bytes], dict[str, str]]:
        """Open a streaming response and keep it alive for the iterator."""
        http_request = request.Request(url, data=body, headers=headers, method="POST")
        try:
            response = request.urlopen(http_request, timeout=timeout)
        except error.HTTPError as exc:
            return exc.code, iter((exc.read(),)), dict(exc.headers.items()) if exc.headers else {}
        response_headers = dict(response.headers.items()) if response.headers else {}

        def chunks() -> Iterable[bytes]:
            try:
                while True:
                    chunk = response.readline()
                    if not chunk:
                        break
                    yield chunk
            finally:
                response.close()

        return response.status, chunks(), response_headers


def _unpack_transport_result(result: Any) -> tuple[Any, Any, dict[str, str]]:
    """Accept legacy ``(status, payload)`` and optional header-bearing results."""
    if not isinstance(result, tuple) or len(result) not in {2, 3}:
        raise ProviderError("model transport returned an invalid response tuple", category="transport_error")
    status, payload = result[0], result[1]
    raw_headers = result[2] if len(result) == 3 else {}
    if raw_headers is None:
        raw_headers = {}
    if not isinstance(raw_headers, dict):
        raise ProviderError("model transport returned invalid response headers", category="transport_error")
    headers = {str(key).lower(): str(value) for key, value in raw_headers.items() if isinstance(key, str)}
    return status, payload, headers


def _is_cancelled(signal: CancellationToken | Callable[[], bool] | None) -> bool:
    if signal is None:
        return False
    if isinstance(signal, CancellationToken):
        return signal.is_cancelled()
    try:
        return bool(signal())
    except Exception:
        # A cancellation callback is untrusted; fail closed if it cannot be
        # evaluated rather than allowing a potentially unsafe continuation.
        return True


async def _run_sync_bounded(function: Any, *args: Any, timeout: float, cancellation: CancellationToken | Callable[[], bool] | None = None, **kwargs: Any) -> Any:
    """Run one blocking transport/parser call with a real async deadline.

    ``asyncio.to_thread`` uses the event loop's executor; cancelling it still
    makes ``asyncio.run`` wait for a misbehaving worker during shutdown.  A
    daemon thread lets the caller return at the configured deadline while the
    untrusted blocking operation is detached.  The worker never receives
    authorization or mutable agent state, and late results are discarded.
    """
    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[Any] = loop.create_future()

    def publish(callback: Any, value: Any) -> None:
        try:
            if not result_future.done():
                callback(value)
        except RuntimeError:
            # The event loop may have closed after a timeout/cancellation.
            return

    def notify(callback: Any, value: Any) -> None:
        try:
            loop.call_soon_threadsafe(publish, callback, value)
        except RuntimeError:
            # The loop may be closed after the bounded wait returned.
            return

    def worker() -> None:
        try:
            value = function(*args, **kwargs)
        except BaseException as exc:
            # A transport/parser runs outside the caller's event loop and is
            # untrusted.  Do not let process-level BaseExceptions (notably
            # SystemExit/KeyboardInterrupt) escape through an awaited future
            # and terminate the host.  Preserve asyncio cancellation, while
            # mapping other BaseExceptions to the provider error boundary.
            if isinstance(exc, (asyncio.CancelledError, ProviderError)):
                safe_exc: BaseException = exc
            elif isinstance(exc, Exception):
                safe_exc = exc
            else:
                safe_exc = ProviderError("model transport worker failed", category="transport_error", retryable=False)
            notify(result_future.set_exception, safe_exc)
        else:
            notify(result_future.set_result, value)

    threading.Thread(target=worker, name="forgecode-transport", daemon=True).start()
    deadline = time.monotonic() + timeout
    # Shield the detached worker future: cancellation/deadline must return to
    # the caller without cancelling a future that a late worker may complete.
    while True:
        if _is_cancelled(cancellation):
            raise ProviderError("model request cancelled", category="cancelled", retryable=False, unresolved=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _BoundedOperationTimeout("bounded operation exceeded timeout")
        try:
            return await asyncio.wait_for(asyncio.shield(result_future), timeout=min(0.05, remaining))
        except asyncio.TimeoutError:
            continue


def _redact(text: str, secret: str | None = None) -> str:
    return redact_text(text, (secret,) if secret else ())


def _error_message(payload: bytes, secret: str | None) -> str:
    text = payload.decode("utf-8", errors="replace")[:2_000]
    try:
        parsed = bounded_json_loads(text)
        if isinstance(parsed, dict):
            error_data = parsed.get("error")
            if isinstance(error_data, dict):
                candidate = error_data.get("message") or error_data.get("type")
                text = str(candidate) if isinstance(candidate, (str, int, float, bool)) else "provider returned an error"
            elif error_data:
                text = str(error_data) if isinstance(error_data, (str, int, float, bool)) else "provider returned an error"
    except (json.JSONDecodeError, ValueError):
        pass
    return _redact(text, secret)


def _normalize_usage(raw: Any, *, stream: bool = False) -> dict[str, int | float]:
    """Keep numeric token counters while tolerating provider detail objects.

    OpenAI-compatible gateways commonly add ``*_details`` objects or null
    counters (DeepSeek does this for cached/reasoning usage).  Those optional
    annotations are not required by the agent loop; strict validation still
    rejects non-finite and negative numeric counters.
    """
    if not isinstance(raw, dict) or len(raw) > 32:
        raise ProviderError("model response usage must be a bounded object", category="protocol_error")
    safe: dict[str, int | float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key or len(key) > 128 or any(ord(ch) < 32 for ch in key):
            raise ProviderError("model response usage has an invalid field name", category="protocol_error")
        if value is None or isinstance(value, dict):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            raise ProviderError("model response usage contains a non-finite number", category="protocol_error")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProviderError("model response usage fields must be finite numbers", category="protocol_error")
        if value < 0 or value > 1_000_000_000_000_000:
            raise ProviderError("model response usage is outside the bounded range", category="protocol_error")
        if isinstance(value, int) and value.bit_length() >= 3_322:
            raise ProviderError("model response usage contains an oversized integer", category="response_limit")
        safe[key] = value
    return safe


def _message_to_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False, allow_nan=False)},
            }
            for call in message.tool_calls
        ]
    return payload


def _tool_schema_to_payload(schema: dict[str, Any]) -> dict[str, Any]:
    """Accept provider-neutral or already wrapped schemas and emit OpenAI shape."""
    if not isinstance(schema, dict):
        raise ProviderError("tool schema must be a JSON object", category="protocol_error")
    if schema.get("type") == "function" and isinstance(schema.get("function"), dict):
        function_name = schema["function"].get("name")
        if not isinstance(function_name, str) or not function_name or len(function_name) > 256 or any(ord(ch) < 32 for ch in function_name):
            raise ProviderError("tool schema function name is invalid", category="protocol_error")
        description = schema["function"].get("description", "")
        if not isinstance(description, str) or len(description) > 4_000 or any(ord(ch) < 32 for ch in description) or not isinstance(schema["function"].get("parameters", {}), dict):
            raise ProviderError("tool schema function fields are invalid", category="protocol_error")
        return schema
    if isinstance(schema.get("name"), str):
        if not schema["name"] or len(schema["name"]) > 256 or any(ord(ch) < 32 for ch in schema["name"]):
            raise ProviderError("tool schema function name is invalid", category="protocol_error")
        description = schema.get("description", "")
        if not isinstance(description, str) or len(description) > 4_000 or any(ord(ch) < 32 for ch in description) or not isinstance(schema.get("parameters", {}), dict):
            raise ProviderError("tool schema function fields are invalid", category="protocol_error")
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
        if not isinstance(call_id, str) or not call_id or len(call_id) > 256 or any(ord(ch) < 32 for ch in call_id):
            raise ProviderError(f"model response tool call {index} has no id", category="protocol_error")
        if call_id in seen_ids:
            raise ProviderError(f"model response repeats tool call id {call_id}", category="protocol_error")
        seen_ids.add(call_id)
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"]:
            raise ProviderError(f"model response tool call {index} has no function name", category="protocol_error")
        arguments = function.get("arguments", "{}")
        if isinstance(arguments, str):
            if len(arguments) > 200_000:
                raise ProviderError(f"tool call {call_id} arguments exceeded the configured size limit", category="response_limit")
            try:
                arguments = bounded_json_loads(arguments or "{}", parse_constant=_reject_nonfinite)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ProviderError(f"tool call {call_id} has invalid JSON arguments", category="protocol_error") from exc
        if not isinstance(arguments, dict):
            raise ProviderError(f"tool call {call_id} arguments must be an object", category="protocol_error")
        _validate_json_value(arguments)
        calls.append(ToolCall(call_id, function["name"], arguments))
    return tuple(calls)


def parse_chat_completion(payload: dict[str, Any]) -> ModelResponse:
    if not isinstance(payload, dict):
        raise ProviderError("model response must be a JSON object", category="protocol_error")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
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
    if finish_reason not in {None, "stop", "length", "tool_calls", "content_filter"}:
        raise ProviderError("model response contains an unsupported finish_reason", category="protocol_error")
    usage = payload.get("usage", {})
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        raise ProviderError("model response usage must be an object", category="protocol_error")
    safe_usage = _normalize_usage(usage)
    if calls and finish_reason is not None and finish_reason != "tool_calls":
        raise ProviderError("tool calls require finish_reason=tool_calls", category="protocol_error")
    if not calls and finish_reason == "tool_calls":
        raise ProviderError("tool_calls finish_reason has no tool calls", category="protocol_error")
    return ModelResponse(Message(role="assistant", content=content, tool_calls=calls), finish_reason, safe_usage)


def _sse_json_events(chunks: Iterable[bytes], *, max_bytes: int = 4_000_000, max_events: int = 2_000, cancellation: CancellationToken | Callable[[], bool] | None = None, allow_duplicate_frames: bool = False, on_text_delta: Callable[[str], None] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """Parse a bounded OpenAI-compatible SSE byte stream.

    We decode only complete UTF-8 lines and complete JSON data frames.  A
    malformed or interrupted stream raises before any assembled tool call is
    returned to the loop.
    """
    buffer = b""
    total = 0
    events: list[dict[str, Any]] = []
    # Keep a canonical representation in addition to the raw bytes.  Servers
    # and proxies can replay the same JSON frame with different whitespace or
    # key ordering; treating that as a new delta could duplicate content or
    # tool arguments and cross the side-effect boundary.
    seen_data_frames: set[str] = set()
    done = False
    for chunk in chunks:
        if _is_cancelled(cancellation):
            raise ProviderError("stream request cancelled", category="cancelled", retryable=False)
        if not isinstance(chunk, (bytes, bytearray)):
            raise ProviderError("stream transport yielded a non-byte chunk", category="stream_protocol_error")
        total += len(chunk)
        if total > max_bytes:
            raise ProviderError("stream response exceeded the configured size limit", category="response_limit")
        buffer += bytes(chunk)
        while b"\n" in buffer:
            raw_line, buffer = buffer.split(b"\n", 1)
            line = raw_line.rstrip(b"\r")
            if not line:
                continue
            if not line.startswith(b"data:"):
                # Ignore bounded SSE comments/fields, but reject arbitrary
                # bytes that could hide a tool fragment.
                if line.startswith((b":", b"event:", b"id:", b"retry:")):
                    continue
                raise ProviderError("malformed SSE frame", category="stream_protocol_error")
            data = line[5:].lstrip()
            if data == b"[DONE]":
                if done:
                    raise ProviderError("SSE stream repeated [DONE]", category="stream_protocol_error")
                done = True
                continue
            if done:
                raise ProviderError("SSE data appeared after [DONE]", category="stream_protocol_error")
            try:
                decoded = data.decode("utf-8")
                payload = bounded_json_loads(decoded, parse_constant=_reject_nonfinite)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ProviderError("malformed SSE JSON frame", category="stream_protocol_error") from exc
            if not isinstance(payload, dict):
                raise ProviderError("SSE frame must contain a JSON object", category="stream_protocol_error")
            # A replayed SSE frame would duplicate content/tool arguments and
            # could trigger an unintended side effect.  Compare canonical JSON
            # rather than raw bytes so harmless whitespace/key-order changes do
            # not bypass the duplicate guard. Usage-only frames are covered by
            # the same check for deterministic diagnostics.
            try:
                canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ProviderError("SSE frame is not JSON-safe", category="stream_protocol_error") from exc
            if canonical in seen_data_frames:
                if allow_duplicate_frames:
                    continue
                raise ProviderError("SSE stream repeated a data frame", category="stream_protocol_error")
            seen_data_frames.add(canonical)
            events.append(payload)
            # Forward visible assistant text as soon as its SSE frame arrives.
            # Full assembly still happens below, so protocol validation and
            # tool execution remain completion-boundary operations.
            if on_text_delta is not None:
                choices = payload.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    delta = choices[0].get("delta")
                    text = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(text, str) and text:
                        try:
                            on_text_delta(text)
                        except Exception:
                            pass
            if len(events) > max_events:
                raise ProviderError("stream contains too many events", category="response_limit")
    if buffer.strip():
        raise ProviderError("stream ended with an incomplete SSE frame", category="stream_protocol_error")
    if not done:
        raise ProviderError("stream ended before [DONE]", category="stream_incomplete")
    return events, done


def assemble_chat_stream(events: Iterable[dict[str, Any]], *, max_content_chars: int = 200_000, max_argument_chars: int = 200_000, cancellation: CancellationToken | Callable[[], bool] | None = None, on_text_delta: Callable[[str], None] | None = None) -> ModelResponse:
    """Assemble deltas and validate complete tool calls before returning."""
    content_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    calls: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    finished = False
    seen_fragments: set[str] = set()
    for event in events:
        if _is_cancelled(cancellation):
            raise ProviderError("stream assembly cancelled", category="cancelled", retryable=False)
        if not isinstance(event, dict):
            raise ProviderError("stream event must be a JSON object", category="stream_protocol_error")
        choices = event.get("choices")
        # OpenAI-compatible providers may send a final usage-only frame with
        # choices=[] before [DONE]. It carries no delta and cannot complete a
        # tool call by itself, so accept it only when usage is a valid object.
        if choices == [] and "usage" in event:
            raw_usage = event.get("usage")
            if not isinstance(raw_usage, dict) or len(raw_usage) > 32:
                raise ProviderError("stream usage must be a bounded object", category="stream_protocol_error")
            usage = _normalize_usage(raw_usage, stream=True)
            continue
        if finished:
            raise ProviderError("stream emitted data after finish_reason", category="stream_protocol_error")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderError("stream frame has invalid choices", category="stream_protocol_error")
        choice = choices[0]
        index = choice.get("index", 0)
        if index != 0:
            raise ProviderError("multiple stream choices are unsupported", category="stream_protocol_error")
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            raise ProviderError("stream delta is not an object", category="stream_protocol_error")
        text = delta.get("content")
        if text is not None:
            if not isinstance(text, str):
                raise ProviderError("stream content delta is not text", category="stream_protocol_error")
            content_parts.append(text)
            if text and on_text_delta is not None:
                try:
                    on_text_delta(text)
                except Exception:
                    # Presentation callbacks must never weaken provider
                    # validation or turn a valid response into a failure.
                    pass
            if sum(len(part) for part in content_parts) > max_content_chars:
                raise ProviderError("stream content exceeded the configured size limit", category="response_limit")
        raw_calls = delta.get("tool_calls", [])
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise ProviderError("stream tool_calls delta must be a list", category="stream_protocol_error")
        for raw in raw_calls:
            if not isinstance(raw, dict) or not isinstance(raw.get("index"), int) or isinstance(raw.get("index"), bool) or raw.get("index") < 0:
                raise ProviderError("stream tool call fragment has no integer index", category="stream_protocol_error")
            call_index = raw["index"]
            try:
                fragment_key = json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ProviderError("stream tool call fragment is not JSON-safe", category="stream_protocol_error") from exc
            if fragment_key in seen_fragments:
                raise ProviderError("stream repeated a tool call fragment", category="stream_protocol_error")
            seen_fragments.add(fragment_key)
            if call_index not in calls:
                if order and call_index <= max(order):
                    raise ProviderError("stream introduced an out-of-order tool index", category="stream_protocol_error")
                calls[call_index] = {"id": None, "name": None, "arguments": ""}
                order.append(call_index)
            current = calls[call_index]
            if raw.get("id") is not None:
                if not isinstance(raw["id"], str) or not raw["id"] or len(raw["id"]) > 256 or any(ord(ch) < 32 for ch in raw["id"]) or (current["id"] is not None and current["id"] != raw["id"]):
                    raise ProviderError("stream tool call id changed or is invalid", category="stream_protocol_error")
                current["id"] = raw["id"]
            function = raw.get("function", {})
            if not isinstance(function, dict):
                raise ProviderError("stream function fragment is invalid", category="stream_protocol_error")
            if function.get("name") is not None:
                if not isinstance(function["name"], str) or (current["name"] is not None and current["name"] != function["name"]):
                    raise ProviderError("stream tool function name changed or is invalid", category="stream_protocol_error")
                current["name"] = function["name"]
            arguments = function.get("arguments", "")
            if not isinstance(arguments, str):
                raise ProviderError("stream tool arguments fragment is not text", category="stream_protocol_error")
            current["arguments"] += arguments
            if len(current["arguments"]) > max_argument_chars:
                raise ProviderError("stream tool arguments exceeded the configured size limit", category="response_limit")
        if choice.get("finish_reason") is not None:
            if not isinstance(choice["finish_reason"], str) or choice["finish_reason"] not in {"stop", "length", "tool_calls", "content_filter"}:
                raise ProviderError("stream finish_reason is invalid", category="stream_protocol_error")
            finish_reason = choice["finish_reason"]
            finished = True
        if event.get("usage") is not None:
            if not isinstance(event.get("usage"), dict):
                raise ProviderError("stream usage must be an object", category="stream_protocol_error")
            if len(event["usage"]) > 32:
                raise ProviderError("stream usage contains too many fields", category="response_limit")
            usage = _normalize_usage(event["usage"], stream=True)
    parsed_calls: list[ToolCall] = []
    seen_ids: set[str] = set()
    for index in order:
        call = calls[index]
        if not isinstance(call["id"], str) or not call["id"] or len(call["id"]) > 256 or any(ord(ch) < 32 for ch in call["id"]) or not isinstance(call["name"], str) or not call["name"]:
            raise ProviderError("stream tool call is incomplete", category="stream_incomplete")
        if call["id"] in seen_ids:
            raise ProviderError("stream repeats a tool call id", category="stream_protocol_error")
        try:
            arguments = bounded_json_loads(call["arguments"] or "{}", parse_constant=_reject_nonfinite)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("stream tool call has incomplete JSON arguments", category="stream_incomplete") from exc
        if not isinstance(arguments, dict):
            raise ProviderError("stream tool arguments must be an object", category="stream_protocol_error")
        _validate_json_value(arguments)
        seen_ids.add(call["id"])
        parsed_calls.append(ToolCall(call["id"], call["name"], arguments))
    if not finished:
        raise ProviderError("stream has no finish_reason", category="stream_incomplete")
    if parsed_calls and finish_reason != "tool_calls":
        raise ProviderError("stream tool calls did not finish with tool_calls", category="stream_protocol_error")
    if not parsed_calls and finish_reason not in {"stop", "length", "content_filter"}:
        raise ProviderError("stream finish_reason is unsupported", category="stream_protocol_error")
    return ModelResponse(Message("assistant", "".join(content_parts), tool_calls=tuple(parsed_calls)), finish_reason, usage)


class OpenAICompatibleProvider:
    provider_name = "openai-compatible"
    def __init__(self, *, api_key: str, base_url: str, model: str, transport: JsonTransport | None = None, timeout: float = 60.0, max_response_bytes: int = 4_000_000, max_request_bytes: int = 4_000_000, max_retries: int = 2, retry_base_delay: float = 0.25, streaming: bool = False, stream_required: bool = False):
        if not isinstance(api_key, str) or not api_key or len(api_key) > 4_096 or any(ord(character) < 0x20 or ord(character) == 0x7F for character in api_key):
            raise ProviderError("FORGECODE_API_KEY is not configured", category="configuration_error")
        if not model:
            raise ProviderError("FORGECODE_MODEL is not configured", category="configuration_error")
        if not base_url:
            raise ProviderError("FORGECODE_BASE_URL is empty", category="configuration_error")
        if not isinstance(base_url, str) or len(base_url) > 512 or any(character.isspace() for character in base_url):
            raise ProviderError("FORGECODE_BASE_URL is invalid", category="configuration_error")
        try:
            parsed_url = urlsplit(base_url)
            hostname = parsed_url.hostname
            _ = parsed_url.port
        except ValueError as exc:
            raise ProviderError("FORGECODE_BASE_URL is invalid", category="configuration_error") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or hostname is None or parsed_url.username is not None or parsed_url.password is not None or parsed_url.query or parsed_url.fragment:
            raise ProviderError("FORGECODE_BASE_URL must be a credential-free http(s) URL", category="configuration_error")
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
        self.streaming = bool(streaming)
        self.stream_required = bool(stream_required)
        self.retry_events: list[dict[str, Any]] = []
        self.attempt_events: list[dict[str, Any]] = []
        self.last_request_id: str | None = None

    @property
    def capabilities(self) -> ModelCapabilities:
        transports = ("json", "sse") if self.streaming else ("json",)
        return ModelCapabilities(streaming=self.streaming, max_input_chars=self.max_request_bytes, max_output_chars=self.max_response_bytes, transports=transports)

    def health(self) -> dict[str, Any]:
        """Return offline configuration diagnostics; never performs a request."""
        return {
            "provider": self.provider_name,
            "model": self.model,
            "base_url": self.base_url,
            "configured": bool(self.api_key and self.model and self.base_url),
            "streaming": self.streaming,
            "stream_required": self.stream_required,
            "capabilities": self.capabilities.to_dict(),
        }

    @classmethod
    def from_environment(cls, *, transport: JsonTransport | None = None) -> "OpenAICompatibleProvider":
        return cls(
            api_key=os.getenv("FORGECODE_API_KEY", ""),
            base_url=os.getenv("FORGECODE_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("FORGECODE_MODEL", ""),
            transport=transport,
        )

    async def complete(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]], context: ProviderContext | None = None) -> ModelResponse:
        request_context = context or ProviderContext()
        request_id = request_context.request_id or uuid.uuid4().hex
        self.last_request_id = request_id
        self.retry_events = []
        self.attempt_events = []
        request_started = time.monotonic()

        def check_request() -> None:
            if request_context.cancelled:
                raise ProviderError("model request cancelled", category="cancelled", retryable=False, request_id=request_id)
            if request_context.deadline_monotonic is not None and request_context.remaining_seconds(self.timeout) <= 0:
                raise ProviderError("model request deadline exceeded", category="deadline_exceeded", retryable=False, request_id=request_id)

        def attempt_started(attempt: int, protocol: str) -> dict[str, Any]:
            item = {
                "request_id": request_id,
                "attempt_id": f"{request_id}:{protocol}:{attempt}",
                "attempt": attempt,
                "protocol": protocol,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "started_monotonic": time.monotonic(),
            }
            self.attempt_events.append(item)
            return item

        def attempt_finished(item: dict[str, Any], *, outcome: str, error_category: str | None = None, unresolved: bool = False) -> None:
            # Do not let an intermediate HTTP response marker hide a later
            # decode/protocol failure. Finishing an attempt is idempotent.
            if "outcome" in item:
                return
            item["ended_at"] = datetime.now(timezone.utc).isoformat()
            item["duration_seconds"] = round(time.monotonic() - float(item.get("started_monotonic", request_started)), 6)
            item["outcome"] = outcome
            if error_category:
                item["error_category"] = error_category
            item["unresolved"] = bool(unresolved)
            item.pop("started_monotonic", None)

        def annotate_error(exc: ProviderError, attempt: int | None = None) -> ProviderError:
            """Attach bounded request/attempt identity to every provider error."""
            if exc.request_id is None:
                exc.request_id = request_id
            if exc.attempt is None and attempt is not None:
                exc.attempt = attempt
            return exc

        try:
            tool_payloads = [_tool_schema_to_payload(schema) for schema in tools]
        except AttributeError as exc:
            raise ProviderError("tool schema must be an object", category="protocol_error") from exc
        try:
            body = json.dumps({"model": self.model, "messages": [_message_to_payload(message) for message in messages], "tools": tool_payloads}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("model request contains invalid JSON values", category="protocol_error", request_id=request_id) from exc
        if len(body) > self.max_request_bytes:
            raise ProviderError("model request exceeded the configured size limit", category="request_limit", request_id=request_id)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.streaming:
            stream_method = getattr(self.transport, "post_stream", None)
            if stream_method is None:
                if self.stream_required:
                    raise ProviderError("streaming is required but transport does not support SSE", category="configuration_error", request_id=request_id)
            else:
                try:
                    try:
                        stream_body = json.dumps({"model": self.model, "messages": [_message_to_payload(message) for message in messages], "tools": tool_payloads, "stream": True}, ensure_ascii=False, allow_nan=False).encode("utf-8")
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ProviderError("model request contains invalid JSON values", category="protocol_error", request_id=request_id) from exc
                    if len(stream_body) > self.max_request_bytes:
                        raise ProviderError("model request exceeded the configured size limit", category="request_limit", request_id=request_id)
                    for attempt in range(1, self.max_retries + 2):
                        check_request()
                        attempt_info = attempt_started(attempt, "sse")
                        try:
                            timeout = request_context.remaining_seconds(self.timeout)
                            if timeout <= 0:
                                raise ProviderError("model request deadline exceeded", category="deadline_exceeded", request_id=request_id)
                            transport_result = await _run_sync_bounded(
                                stream_method,
                                f"{self.base_url}/chat/completions",
                                {**headers, "Accept": "text/event-stream"},
                                stream_body,
                                timeout,
                                timeout=timeout,
                                cancellation=request_context.cancellation_token or request_context.cancellation_requested,
                            )
                            status, chunks, response_headers = _unpack_transport_result(transport_result)
                        except asyncio.CancelledError:
                            attempt_finished(attempt_info, outcome="cancelled", error_category="cancelled", unresolved=True)
                            raise
                        except (TimeoutError, error.URLError, OSError) as exc:
                            unresolved = bool(getattr(exc, "unresolved", False))
                            attempt_finished(attempt_info, outcome="error", error_category="stream_transport_error", unresolved=unresolved)
                            # A bounded timeout means the synchronous
                            # transport worker may still be running. Retrying
                            # would issue a duplicate request while that
                            # unresolved attempt can still reach the provider.
                            # Keep the attempt unresolved and let the caller's
                            # recovery boundary decide what to do.
                            if attempt <= self.max_retries and not unresolved:
                                await self._retry(attempt, "stream_transport_error", str(exc), context=request_context, request_id=request_id)
                                continue
                            raise ProviderError(_redact(f"stream request failed: {exc}", self.api_key), category="stream_error", retryable=True, attempt=attempt, request_id=request_id, unresolved=unresolved) from exc
                        except ProviderError as exc:
                            annotate_error(exc, attempt)
                            attempt_finished(attempt_info, outcome="error", error_category=exc.category, unresolved=exc.unresolved)
                            raise
                        except (AttributeError, NotImplementedError):
                            attempt_finished(attempt_info, outcome="error", error_category="not_implemented")
                            raise
                        except Exception as exc:
                            attempt_finished(attempt_info, outcome="error", error_category="stream_transport_error")
                            raise ProviderError(_redact(f"stream request failed: {type(exc).__name__}: {exc}", self.api_key), category="stream_error", retryable=False, attempt=attempt, request_id=request_id) from exc
                        if isinstance(status, bool) or not isinstance(status, int):
                            attempt_finished(attempt_info, outcome="error", error_category="stream_error")
                            raise ProviderError("stream transport returned an invalid HTTP status", category="stream_error", attempt=attempt, request_id=request_id)
                        if status in {408, 429} or 500 <= status <= 599:
                            if attempt <= self.max_retries:
                                attempt_finished(attempt_info, outcome="retry", error_category=f"stream_http_{status}")
                                await self._retry(attempt, f"stream_http_{status}", f"HTTP {status}", response_headers.get("retry-after"), context=request_context, request_id=request_id)
                                continue
                        if status in {404, 405, 501} and not self.stream_required:
                            # Some OpenAI-compatible gateways expose chat
                            # completions but not SSE. In optional/auto mode,
                            # treat this as a capability signal and fall back
                            # to the bounded JSON request below.
                            attempt_finished(attempt_info, outcome="fallback", error_category=f"stream_http_{status}")
                            break
                        if status < 200 or status >= 300:
                            attempt_finished(attempt_info, outcome="error", error_category="http_error")
                            raise ProviderError(f"model returned HTTP {status}", category="http_error", retryable=status in {408, 429} or 500 <= status <= 599, status_code=status, attempt=attempt, request_id=request_id)
                        try:
                            timeout = request_context.remaining_seconds(self.timeout)
                            if timeout <= 0:
                                raise ProviderError("model request deadline exceeded", category="deadline_exceeded", request_id=request_id)
                            events, _done = await _run_sync_bounded(
                                _sse_json_events, chunks,
                                max_bytes=self.max_response_bytes,
                                allow_duplicate_frames="deepseek.com" in self.base_url.lower(),
                                on_text_delta=request_context.on_text_delta,
                                timeout=timeout,
                                cancellation=request_context.cancellation_token or request_context.cancellation_requested,
                            )
                            response = assemble_chat_stream(events, cancellation=request_context.cancellation_token or request_context.cancellation_requested)
                            attempt_finished(attempt_info, outcome="success")
                            return response
                        except asyncio.CancelledError:
                            attempt_finished(attempt_info, outcome="cancelled", error_category="cancelled", unresolved=True)
                            raise
                        except ProviderError as exc:
                            annotate_error(exc, attempt)
                            attempt_finished(attempt_info, outcome="error", error_category=exc.category, unresolved=exc.unresolved)
                            # A malformed or truncated SSE response is a
                            # provider/proxy protocol failure, not a tool
                            # execution failure: no model tool call has been
                            # handed to AgentLoop yet. Retry it within the
                            # existing bounded request budget.
                            if (exc.category in {"stream_protocol_error", "stream_incomplete"}
                                    and attempt <= self.max_retries
                                    and not exc.unresolved):
                                await self._retry(attempt, exc.category, str(exc), context=request_context, request_id=request_id)
                                continue
                            raise
                        except (TimeoutError, error.URLError, OSError) as exc:
                            unresolved = bool(getattr(exc, "unresolved", False))
                            attempt_finished(attempt_info, outcome="error", error_category="stream_transport_error", unresolved=unresolved)
                            if attempt <= self.max_retries and not unresolved:
                                await self._retry(attempt, "stream_transport_error", str(exc), context=request_context, request_id=request_id)
                                continue
                            raise ProviderError(_redact(f"stream request failed: {exc}", self.api_key), category="stream_error", retryable=True, attempt=attempt, request_id=request_id, unresolved=unresolved) from exc
                        except Exception as exc:
                            attempt_finished(attempt_info, outcome="error", error_category="stream_error")
                            raise ProviderError(_redact(f"stream request failed: {type(exc).__name__}: {exc}", self.api_key), category="stream_error", retryable=False, attempt=attempt, request_id=request_id) from exc
                except ProviderError:
                    raise
                except (AttributeError, NotImplementedError) as exc:
                    if self.stream_required:
                        raise ProviderError("streaming is required but transport does not implement SSE", category="configuration_error", request_id=request_id) from exc
                    # A transport may expose an optional method that is not
                    # implemented by a particular backend.  In auto/on mode
                    # fall through to the bounded non-stream request; only
                    # malformed/partial streams remain fatal above.
                    pass
                except Exception as exc:
                    raise ProviderError(_redact(f"stream request failed: {type(exc).__name__}: {exc}", self.api_key), category="stream_error") from exc
        if self.stream_required:
            raise ProviderError("streaming is required but no stream was requested", category="configuration_error", request_id=request_id)
        for attempt in range(1, self.max_retries + 2):
            check_request()
            attempt_info = attempt_started(attempt, "json")
            try:
                timeout = request_context.remaining_seconds(self.timeout)
                if timeout <= 0:
                    raise ProviderError("model request deadline exceeded", category="deadline_exceeded", request_id=request_id)
                transport_result = await _run_sync_bounded(
                    self.transport.post_json,
                    f"{self.base_url}/chat/completions",
                    headers,
                    body,
                    timeout,
                    timeout=timeout,
                    cancellation=request_context.cancellation_token or request_context.cancellation_requested,
                )
                status, response_body, response_headers = _unpack_transport_result(transport_result)
            except asyncio.CancelledError:
                attempt_finished(attempt_info, outcome="cancelled", error_category="cancelled", unresolved=True)
                raise
            except (TimeoutError, error.URLError, OSError) as exc:
                unresolved = bool(getattr(exc, "unresolved", False))
                attempt_finished(attempt_info, outcome="error", error_category="transport_error", unresolved=unresolved)
                retryable = True
                if attempt <= self.max_retries and not unresolved:
                    await self._retry(attempt, "transport_error", str(exc), context=request_context, request_id=request_id)
                    continue
                raise ProviderError(_redact(f"model request failed: {exc}", self.api_key), category="transport_error", retryable=retryable, attempt=attempt, request_id=request_id, unresolved=unresolved) from exc
            except ProviderError as exc:
                annotate_error(exc, attempt)
                attempt_finished(attempt_info, outcome="error", error_category=exc.category, unresolved=exc.unresolved)
                raise
            except Exception as exc:
                attempt_finished(attempt_info, outcome="error", error_category="transport_error")
                raise ProviderError(_redact(f"model request failed: {type(exc).__name__}: {exc}", self.api_key), category="transport_error", retryable=False, attempt=attempt, request_id=request_id) from exc
            if not isinstance(response_body, (bytes, bytearray)):
                attempt_finished(attempt_info, outcome="error", error_category="transport_error")
                raise ProviderError("model transport returned a non-byte response", category="transport_error", attempt=attempt, request_id=request_id)
            if isinstance(status, bool) or not isinstance(status, int):
                attempt_finished(attempt_info, outcome="error", error_category="transport_error")
                raise ProviderError("model transport returned an invalid HTTP status", category="transport_error", attempt=attempt, request_id=request_id)
            retryable_status = status in {408, 429} or 500 <= status <= 599
            if retryable_status and attempt <= self.max_retries:
                attempt_finished(attempt_info, outcome="retry", error_category=f"http_{status}")
                await self._retry(attempt, f"http_{status}", f"HTTP {status}", response_headers.get("retry-after"), context=request_context, request_id=request_id)
                continue
            break
        try:
            if not isinstance(response_body, (bytes, bytearray)):
                raise ProviderError("model transport returned a non-byte response", category="transport_error", attempt=attempt)
            if isinstance(status, bool) or not isinstance(status, int):
                raise ProviderError("model transport returned an invalid HTTP status", category="transport_error", attempt=attempt)
            if len(response_body) > self.max_response_bytes:
                raise ProviderError("model response exceeded the configured size limit", category="response_limit", attempt=attempt, request_id=request_id)
            if status < 200 or status >= 300:
                raise ProviderError(f"model returned HTTP {status}: {_error_message(response_body, self.api_key)}", category="http_error", retryable=status in {408, 429} or 500 <= status <= 599, status_code=status, attempt=attempt, request_id=request_id)
            try:
                payload = bounded_json_loads(response_body.decode("utf-8"), parse_constant=_reject_nonfinite)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ProviderError("model returned malformed JSON", category="protocol_error", attempt=attempt, request_id=request_id) from exc
            if not isinstance(payload, dict):
                raise ProviderError("model response must be a JSON object", category="protocol_error", attempt=attempt, request_id=request_id)
            if payload.get("error"):
                safe_message = _redact(str(payload.get("error"))[:2_000], self.api_key)
                raise ProviderError(f"model returned an error object: {safe_message}", category="provider_error", attempt=attempt, request_id=request_id)
            try:
                response = parse_chat_completion(payload)
            except ProviderError as exc:
                raise ProviderError(str(exc), category=exc.category, retryable=exc.retryable, status_code=exc.status_code, attempt=attempt, request_id=request_id) from exc
            attempt_finished(attempt_info, outcome="success")
            return response
        except ProviderError as exc:
            annotate_error(exc, attempt)
            attempt_finished(attempt_info, outcome="error", error_category=exc.category, unresolved=exc.unresolved)
            raise

    async def _retry(self, attempt: int, category: str, reason: str, retry_after: str | None = None, *, context: ProviderContext | None = None, request_id: str | None = None) -> None:
        # Jitter prevents synchronized clients while the cap keeps tests and
        # interactive use bounded. Do not retry local side effects here.
        delay = min(4.0, self.retry_base_delay * (2 ** (attempt - 1)))
        if retry_after is not None:
            try:
                advertised = float(retry_after.strip())
                if math.isfinite(advertised) and advertised >= 0:
                    delay = min(4.0, advertised)
            except (AttributeError, TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(str(retry_after).strip())
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    advertised = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    if math.isfinite(advertised) and advertised >= 0:
                        delay = min(4.0, advertised)
                except (TypeError, ValueError, OverflowError):
                    pass
        jitter = random.SystemRandom().uniform(0, delay * 0.25) if delay else 0.0
        wait = min(4.0, delay + jitter)
        event = {"request_id": request_id, "attempt_id": f"{request_id}:{category}:{attempt}" if request_id else None, "attempt": attempt, "next_attempt": attempt + 1, "category": category, "delay_seconds": round(wait, 3), "reason": _redact(reason, self.api_key)}
        if retry_after is not None:
            event["retry_after"] = _redact(str(retry_after)[:64], self.api_key)
        self.retry_events.append(event)
        if wait:
            remaining = context.remaining_seconds(wait) if context is not None else wait
            if remaining <= 0:
                raise ProviderError("model request deadline exceeded during retry backoff", category="deadline_exceeded", retryable=False, attempt=attempt, request_id=request_id)
            if context is None or context.cancellation_token is None and context.cancellation_requested is None:
                await asyncio.sleep(remaining)
                return
            # Poll the token in short intervals so a cancelled retry does not
            # sleep for the entire advertised delay.
            deadline = time.monotonic() + remaining
            signal = context.cancellation_token or context.cancellation_requested
            while True:
                if _is_cancelled(signal):
                    raise ProviderError("model request cancelled during retry backoff", category="cancelled", retryable=False, attempt=attempt, request_id=request_id)
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                await asyncio.sleep(min(0.05, left))
