"""Provider-neutral message and tool-calling contracts."""

from dataclasses import dataclass, field
import math
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    role: str
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ModelResponse:
    message: Message
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


def is_valid_response(response: Any) -> bool:
    """Validate the small provider-neutral response contract at the loop boundary."""
    if not isinstance(response, ModelResponse) or not isinstance(response.message, Message):
        return False
    if not isinstance(response.message.role, str) or response.message.role != "assistant" or not isinstance(response.message.content, str):
        return False
    if len(response.message.content) > 200_000:
        return False
    if response.finish_reason is not None and not isinstance(response.finish_reason, str):
        return False
    if response.finish_reason not in {None, "stop", "length", "tool_calls", "content_filter"}:
        return False
    if not isinstance(response.usage, dict):
        return False
    if len(response.usage) > 32:
        return False
    for value in response.usage.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
    if not isinstance(response.message.tool_calls, tuple):
        return False
    seen_ids: set[str] = set()
    for call in response.message.tool_calls:
        if not isinstance(call, ToolCall) or not isinstance(call.id, str) or not call.id or len(call.id) > 256 or call.id in seen_ids or not isinstance(call.name, str) or not call.name or len(call.name) > 256 or not isinstance(call.arguments, dict):
            return False
        seen_ids.add(call.id)
        if not _valid_json_value(call.arguments):
            return False
    if response.message.tool_calls and response.finish_reason not in {None, "tool_calls"}:
        return False
    if response.finish_reason == "tool_calls" and not response.message.tool_calls:
        return False
    return True


def _valid_json_value(value: Any, *, depth: int = 0, budget: list[int] | None = None, seen: set[int] | None = None) -> bool:
    """Bound provider-neutral tool arguments before registry dispatch."""
    if budget is None:
        budget = [100_000]
    if seen is None:
        seen = set()
    if depth > 24:
        return False
    budget[0] -= 1
    if budget[0] < 0:
        return False
    if value is None or isinstance(value, (str, int, bool)):
        return not isinstance(value, str) or len(value) <= 200_000
    if isinstance(value, float):
        return math.isfinite(value)
    object_id = id(value)
    if object_id in seen:
        return False
    if isinstance(value, dict):
        if len(value) > 10_000 or any(not isinstance(key, str) or len(key) > 1_000 for key in value):
            return False
        seen.add(object_id)
        try:
            return all(_valid_json_value(item, depth=depth + 1, budget=budget, seen=seen) for item in value.values())
        finally:
            seen.discard(object_id)
    if isinstance(value, list):
        if len(value) > 10_000:
            return False
        seen.add(object_id)
        try:
            return all(_valid_json_value(item, depth=depth + 1, budget=budget, seen=seen) for item in value)
        finally:
            seen.discard(object_id)
    return False


class ModelProvider(Protocol):
    async def complete(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]]) -> ModelResponse:
        """Return the next assistant message, possibly containing tool calls."""


class ProviderError(RuntimeError):
    """A safe, user-facing model provider or protocol error."""

    def __init__(self, message: str, *, category: str = "provider_error", retryable: bool = False, status_code: int | None = None, attempt: int | None = None):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.attempt = attempt
