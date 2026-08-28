"""Small, fail-closed lifecycle hook registry.

Hooks are observers by default.  A hook may be declared ``fail_closed`` to
stop the operation when it raises or exceeds its time budget; it still cannot
grant permissions, invoke an agent recursively, or mutate an approval result.
"""

from __future__ import annotations

from dataclasses import dataclass
import contextvars
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import time
from typing import Any, Callable, Iterable

from .security.redaction import redact_value


MAX_HOOKS = 64
MAX_EVENT_FIELDS = 128


@dataclass(frozen=True)
class Hook:
    name: str
    event: str
    callback: Callable[[dict[str, Any]], Any]
    failure_policy: str = "observe_only"
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or len(self.name) > 128:
            raise ValueError("hook name must be bounded text")
        if not isinstance(self.event, str) or not self.event or len(self.event) > 128:
            raise ValueError("hook event must be bounded text")
        if self.failure_policy not in {"observe_only", "fail_closed"}:
            raise ValueError("hook failure_policy must be observe_only or fail_closed")
        if not callable(self.callback):
            raise ValueError("hook callback must be callable")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise ValueError("hook timeout_seconds must be between 0 and 30")


@dataclass(frozen=True)
class HookIssue:
    hook: str
    event: str
    error: str
    blocked: bool
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook": self.hook,
            "event": self.event,
            "error": self.error,
            "blocked": self.blocked,
            "duration_seconds": self.duration_seconds,
        }


class HookRegistry:
    def __init__(self, hooks: Iterable[Hook] = (), *, max_hooks: int = MAX_HOOKS):
        if isinstance(max_hooks, bool) or not isinstance(max_hooks, int) or not 1 <= max_hooks <= MAX_HOOKS:
            raise ValueError("max_hooks must be between 1 and 64")
        self.max_hooks = max_hooks
        self._hooks: list[Hook] = []
        self._active: contextvars.ContextVar[bool] = contextvars.ContextVar("forgecode_hook_active", default=False)
        for hook in hooks:
            self.register(hook)

    def register(self, hook: Hook) -> None:
        if len(self._hooks) >= self.max_hooks:
            raise ValueError("hook count exceeds safety limit")
        if any(item.name == hook.name for item in self._hooks):
            raise ValueError(f"duplicate hook: {hook.name}")
        self._hooks.append(hook)

    def names(self) -> tuple[str, ...]:
        return tuple(hook.name for hook in self._hooks)

    def emit(self, event: str, payload: dict[str, Any]) -> tuple[HookIssue, ...]:
        if not isinstance(payload, dict) or len(payload) > MAX_EVENT_FIELDS:
            return (HookIssue("registry", event, "invalid hook payload", True, 0.0),)
        if self._active.get():
            return (HookIssue("registry", event, "recursive hook invocation blocked", True, 0.0),)
        token = self._active.set(True)
        issues: list[HookIssue] = []
        try:
            safe_payload = redact_value({"event": event, **payload})
            for hook in self._hooks:
                if hook.event not in {event, "*"}:
                    continue
                started = time.monotonic()
                blocked = False
                error_text = ""
                try:
                    # Run untrusted callbacks in a bounded worker so a slow
                    # observer cannot stall the agent indefinitely.  A Python
                    # thread cannot be forcibly killed; after timeout it is
                    # detached and its result is ignored, while fail-closed
                    # callers still receive a blocking issue immediately.
                    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="forgecode-hook")
                    callback_context = contextvars.copy_context()
                    future = executor.submit(callback_context.run, hook.callback, dict(safe_payload))
                    try:
                        future.result(timeout=hook.timeout_seconds)
                    except FutureTimeoutError as exc:
                        future.cancel()
                        raise TimeoutError("hook exceeded timeout") from exc
                    finally:
                        # Do not wait for a callback that timed out.  Its
                        # eventual side effects are outside ForgeCode's
                        # control, so fail-closed hooks must be used for
                        # policies where that distinction matters.
                        executor.shutdown(wait=False, cancel_futures=True)
                    elapsed = time.monotonic() - started
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    blocked = hook.failure_policy == "fail_closed"
                    error_text = type(exc).__name__
                    issues.append(HookIssue(hook.name, event, error_text, blocked, round(elapsed, 6)))
        finally:
            self._active.reset(token)
        return tuple(issues)


__all__ = ["Hook", "HookIssue", "HookRegistry"]
