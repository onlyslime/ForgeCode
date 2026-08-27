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


class ModelProvider(Protocol):
    async def complete(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]]) -> ModelResponse:
        """Return the next assistant message, possibly containing tool calls."""
