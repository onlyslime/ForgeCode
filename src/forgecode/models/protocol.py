"""Provider-neutral message and tool-calling contracts."""

from dataclasses import dataclass, field
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
    if not isinstance(response.message.role, str) or not isinstance(response.message.content, str):
        return False
    if response.finish_reason is not None and not isinstance(response.finish_reason, str):
        return False
    if not isinstance(response.usage, dict):
        return False
    if not isinstance(response.message.tool_calls, tuple):
        return False
    for call in response.message.tool_calls:
        if not isinstance(call, ToolCall) or not isinstance(call.id, str) or not call.id or not isinstance(call.name, str) or not call.name or not isinstance(call.arguments, dict):
            return False
    return True


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
