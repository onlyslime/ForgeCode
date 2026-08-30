"""Provider-neutral message and tool-calling contracts."""

from dataclasses import dataclass, field, asdict
import math
import threading
import time
from typing import Any, Callable, Protocol, Sequence


class CancellationToken:
    """Thread-safe cooperative cancellation signal shared by a run.

    Providers and synchronous tools may poll this object without depending on
    asyncio.  Cancellation is idempotent and carries only a bounded,
    non-sensitive reason for diagnostics.
    """

    __slots__ = ("_event", "_lock", "_reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = "cancelled"

    def cancel(self, reason: str = "cancelled") -> bool:
        """Set the token; return ``True`` only for the first request."""
        with self._lock:
            first = not self._event.is_set()
            if first:
                safe_reason = str(reason or "cancelled")[:256]
                self._reason = safe_reason
                self._event.set()
            return first

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def cancelled(self) -> bool:
        return self.is_cancelled()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for cancellation, returning whether it was requested."""
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
                raise ValueError("timeout must be a finite non-negative number")
        return self._event.wait(timeout)


@dataclass(frozen=True)
class ProviderContext:
    """Deadline and cancellation metadata passed to provider adapters."""

    deadline_monotonic: float | None = None
    cancellation_token: CancellationToken | None = None
    cancellation_requested: Callable[[], bool] | None = None
    request_id: str = ""
    on_text_delta: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        if self.deadline_monotonic is not None and (
            isinstance(self.deadline_monotonic, bool)
            or not isinstance(self.deadline_monotonic, (int, float))
            or not math.isfinite(self.deadline_monotonic)
        ):
            raise ValueError("deadline_monotonic must be a finite number or None")
        if not isinstance(self.request_id, str) or len(self.request_id) > 256 or any(ord(ch) < 32 or ord(ch) == 127 for ch in self.request_id):
            raise ValueError("request_id must be bounded text")
        if self.on_text_delta is not None and not callable(self.on_text_delta):
            raise ValueError("on_text_delta must be callable or None")
        if self.cancellation_requested is not None and not callable(self.cancellation_requested):
            raise ValueError("cancellation_requested must be callable or None")

    @property
    def cancelled(self) -> bool:
        token_cancelled = bool(self.cancellation_token and self.cancellation_token.is_cancelled())
        try:
            callback_cancelled = bool(self.cancellation_requested and self.cancellation_requested())
        except Exception:
            # A cancellation predicate is untrusted.  A failure to evaluate
            # it must fail closed instead of allowing a provider request to
            # continue past an unknown safety boundary.
            callback_cancelled = self.cancellation_requested is not None
        return token_cancelled or callback_cancelled

    def remaining_seconds(self, requested: float) -> float:
        if isinstance(requested, bool) or not isinstance(requested, (int, float)) or not math.isfinite(requested) or requested < 0:
            raise ValueError("requested timeout must be a finite non-negative number")
        if self.deadline_monotonic is None:
            return float(requested)
        return max(0.0, min(float(requested), self.deadline_monotonic - time.monotonic()))


def cancellation_requested(context: ProviderContext | None) -> bool:
    """Return a defensive cancellation check for optional provider contexts."""
    return bool(context and context.cancelled)


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
    if response.message.tool_call_id is not None and (not isinstance(response.message.tool_call_id, str) or not response.message.tool_call_id or len(response.message.tool_call_id) > 256 or any(ord(ch) < 32 for ch in response.message.tool_call_id)):
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
    for key, value in response.usage.items():
        if not isinstance(key, str) or not key or len(key) > 128 or any(ord(ch) < 32 for ch in key):
            return False
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        # Usage counters represent consumed resources.  Reject negative
        # values at the provider-neutral boundary as well as in concrete
        # adapters, so custom providers cannot poison run metrics.
        if value < 0 or value > 1_000_000_000_000_000:
            return False
        if isinstance(value, int) and value.bit_length() >= 3_322:
            return False
    if not isinstance(response.message.tool_calls, tuple):
        return False
    seen_ids: set[str] = set()
    for call in response.message.tool_calls:
        if not isinstance(call, ToolCall) or not isinstance(call.id, str) or not call.id or len(call.id) > 256 or any(ord(ch) < 32 for ch in call.id) or call.id in seen_ids or not isinstance(call.name, str) or not call.name or len(call.name) > 256 or any(ord(ch) < 32 for ch in call.name) or not isinstance(call.arguments, dict):
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
        # Use bit_length instead of decimal conversion: Python deliberately
        # limits int-to-str for very large values, and validation must never
        # raise that implementation detail for untrusted provider output.
        if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() >= 3_322:
            return False
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
    async def complete(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]], context: ProviderContext | None = None) -> ModelResponse:
        """Return the next assistant message, possibly containing tool calls."""


class ProviderError(RuntimeError):
    """A safe, user-facing model provider or protocol error."""

    def __init__(self, message: str, *, category: str = "provider_error", retryable: bool = False, status_code: int | None = None, attempt: int | None = None, request_id: str | None = None, unresolved: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.attempt = attempt
        self.request_id = request_id
        # True means a blocking transport/worker may still be running after
        # the bounded caller returned.  Callers must not treat that attempt
        # as completed or replay its side effects automatically.
        self.unresolved = bool(unresolved)

    def to_dict(self) -> dict[str, Any]:
        """Return bounded diagnostic fields safe for envelopes and telemetry."""
        return {
            "category": self.category,
            "retryable": self.retryable,
            "status_code": self.status_code,
            "attempt": self.attempt,
            "request_id": self.request_id,
            "unresolved": self.unresolved,
            "message": str(self)[:500],
        }


@dataclass(frozen=True)
class ModelCapabilities:
    """Provider-neutral declaration used by diagnostics and clients."""

    tool_calling: bool = True
    json_mode: bool = False
    streaming: bool = False
    max_input_chars: int = 400_000
    max_output_chars: int = 200_000
    supports_reasoning: bool = False
    supports_temperature: bool = True
    transports: tuple[str, ...] = ("json",)

    def __post_init__(self) -> None:
        for name in ("tool_calling", "json_mode", "streaming", "supports_reasoning", "supports_temperature"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        for name in ("max_input_chars", "max_output_chars"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 10_000_000:
                raise ValueError(f"{name} must be an integer between 1 and 10000000")
        if not isinstance(self.transports, tuple) or not self.transports or len(self.transports) > 8:
            raise ValueError("transports must be a non-empty tuple of at most 8 items")
        if any(not isinstance(item, str) or not item or len(item) > 32 for item in self.transports):
            raise ValueError("transports must contain bounded non-empty strings")
        if len(set(self.transports)) != len(self.transports):
            raise ValueError("transports must not contain duplicates")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
