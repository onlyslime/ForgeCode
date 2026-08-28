"""Small, fail-closed lifecycle hook registry.

Hooks are observers by default.  A hook may be declared ``fail_closed`` to
stop the operation when it raises or exceeds its time budget; it still cannot
grant permissions, invoke an agent recursively, or mutate an approval result.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import contextvars
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
import time
import uuid
from typing import Any, Callable, Iterable

from .security.redaction import redact_value
from .models.protocol import CancellationToken


MAX_HOOKS = 64
MAX_EVENT_FIELDS = 128
MAX_HISTORY = 512


@dataclass(frozen=True)
class Hook:
    name: str
    event: str
    callback: Callable[[dict[str, Any]], Any]
    failure_policy: str = "observe_only"
    timeout_seconds: float = 2.0
    cleanup: Callable[[dict[str, Any]], Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or len(self.name) > 128:
            raise ValueError("hook name must be bounded text")
        if not isinstance(self.event, str) or not self.event or len(self.event) > 128:
            raise ValueError("hook event must be bounded text")
        if self.failure_policy not in {"observe_only", "fail_closed"}:
            raise ValueError("hook failure_policy must be observe_only or fail_closed")
        if not callable(self.callback):
            raise ValueError("hook callback must be callable")
        if self.cleanup is not None and not callable(self.cleanup):
            raise ValueError("hook cleanup must be callable")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise ValueError("hook timeout_seconds must be between 0 and 30")


@dataclass(frozen=True)
class HookIssue:
    hook: str
    event: str
    error: str
    blocked: bool
    duration_seconds: float
    unresolved: bool = False
    correlation_id: str = ""
    failure_policy: str = "observe_only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook": self.hook,
            "event": self.event,
            "error": self.error,
            "blocked": self.blocked,
            "duration_seconds": self.duration_seconds,
            "unresolved": self.unresolved,
            "correlation_id": self.correlation_id,
            "failure_policy": self.failure_policy,
        }


def _cancelled(signal: CancellationToken | Callable[[], bool] | None) -> bool:
    if signal is None:
        return False
    if isinstance(signal, CancellationToken):
        return signal.is_cancelled()
    try:
        return bool(signal())
    except Exception:
        return True


class HookRegistry:
    def __init__(self, hooks: Iterable[Hook] = (), *, max_hooks: int = MAX_HOOKS):
        if isinstance(max_hooks, bool) or not isinstance(max_hooks, int) or not 1 <= max_hooks <= MAX_HOOKS:
            raise ValueError("max_hooks must be between 1 and 64")
        self.max_hooks = max_hooks
        self._hooks: list[Hook] = []
        self._history: list[dict[str, Any]] = []
        self._cleanup_done = False
        self._history_lock = __import__("threading").RLock()
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

    def history(self, *, limit: int = MAX_HISTORY) -> tuple[dict[str, Any], ...]:
        """Return bounded, redacted lifecycle evidence from this registry.

        History is an observational buffer for callers that need to persist
        hook evidence into a session/review.  It contains no callback return
        values and never exposes raw payload secrets; callers receive copies
        so mutating a returned record cannot alter subsequent evidence.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_HISTORY:
            raise ValueError("history limit must be between 1 and 512")
        # History records contain nested payload/issue lists.  A shallow copy
        # would let a caller mutate a nested value and thereby rewrite the
        # registry's durable-in-process evidence.  Return an independent deep
        # snapshot while retaining the existing bounded/redacted shape.
        return tuple(copy.deepcopy(item) for item in self._history[-limit:])

    def cleanup(
        self,
        payload: dict[str, Any] | None = None,
        *,
        cancellation: CancellationToken | Callable[[], bool] | None = None,
        correlation_id: str | None = None,
    ) -> tuple[HookIssue, ...]:
        """Run declared cleanup callbacks at most once.

        Cleanup is deliberately separate from normal event emission.  A
        timeout or exception is retained as an unresolved/fail-closed issue;
        a second call is a no-op, which prevents double-release side effects
        during both normal shutdown and recovery handling.
        """
        with self._history_lock:
            if self._cleanup_done:
                return ()
            self._cleanup_done = True
        event = "cleanup"
        correlation = correlation_id or (payload or {}).get("correlation_id") or f"cleanup:{uuid.uuid4().hex}"
        if not isinstance(correlation, str) or not correlation or len(correlation) > 256:
            issue = HookIssue("registry", event, "invalid hook correlation id", True, 0.0, False, "", "fail_closed")
            self._record_history(event, "", payload or {}, (issue,))
            return (issue,)
        issues: list[HookIssue] = []
        safe_payload = redact_value({**(payload or {}), "event": event, "correlation_id": correlation})
        for hook in self._hooks:
            callback = hook.cleanup
            if callback is None:
                continue
            started = time.monotonic()
            unresolved = False
            try:
                if _cancelled(cancellation):
                    raise TimeoutError("cleanup cancelled")
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="forgecode-hook-cleanup")
                try:
                    future = executor.submit(callback, dict(safe_payload))
                    future.result(timeout=hook.timeout_seconds)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
            except FutureTimeoutError:
                unresolved = True
                issues.append(HookIssue(hook.name, event, "TimeoutError", hook.failure_policy == "fail_closed", round(time.monotonic() - started, 6), unresolved, correlation, hook.failure_policy))
            except Exception as exc:
                issues.append(HookIssue(hook.name, event, type(exc).__name__, hook.failure_policy == "fail_closed", round(time.monotonic() - started, 6), unresolved, correlation, hook.failure_policy))
        self._record_history(event, correlation, payload or {}, issues)
        return tuple(issues)

    def _record_history(self, event: str, correlation_id: str, payload: dict[str, Any] | None, issues: Iterable[HookIssue]) -> None:
        safe_payload = redact_value(payload or {})
        if isinstance(safe_payload, dict):
            safe_payload = {str(key): value for key, value in list(safe_payload.items())[:MAX_EVENT_FIELDS]}
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event[:128],
            "correlation_id": correlation_id[:256],
            "payload": safe_payload,
            "issues": [item.to_dict() for item in tuple(issues)[:MAX_EVENT_FIELDS]],
        }
        with self._history_lock:
            self._history.append(record)
            if len(self._history) > MAX_HISTORY:
                del self._history[: len(self._history) - MAX_HISTORY]

    def emit(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        cancellation: CancellationToken | Callable[[], bool] | None = None,
        correlation_id: str | None = None,
    ) -> tuple[HookIssue, ...]:
        if not isinstance(event, str) or not event or len(event) > 128:
            issue = HookIssue("registry", str(event)[:128], "invalid hook event", True, 0.0, False, correlation_id or "", "fail_closed")
            self._record_history(str(event), correlation_id or "", payload if isinstance(payload, dict) else {}, (issue,))
            return (issue,)
        if not isinstance(payload, dict) or len(payload) > MAX_EVENT_FIELDS:
            issue = HookIssue("registry", event, "invalid hook payload", True, 0.0, False, correlation_id or "", "fail_closed")
            self._record_history(event, correlation_id or "", {}, (issue,))
            return (issue,)
        if self._active.get():
            issue = HookIssue("registry", event, "recursive hook invocation blocked", True, 0.0, False, correlation_id or "", "fail_closed")
            self._record_history(event, correlation_id or "", payload, (issue,))
            return (issue,)
        inherited_correlation = payload.get("correlation_id")
        if correlation_id is None and isinstance(inherited_correlation, str):
            correlation_id = inherited_correlation
        if correlation_id is None:
            request_id = payload.get("request_id")
            correlation_id = request_id if isinstance(request_id, str) and request_id else f"{event}:{uuid.uuid4().hex}"
        if not isinstance(correlation_id, str) or not correlation_id or len(correlation_id) > 256:
            issue = HookIssue("registry", event, "invalid hook correlation id", True, 0.0, False, "", "fail_closed")
            self._record_history(event, "", payload, (issue,))
            return (issue,)
        token = self._active.set(True)
        issues: list[HookIssue] = []
        try:
            safe_payload = redact_value({"event": event, **payload, "correlation_id": correlation_id})
            for hook in self._hooks:
                if hook.event not in {event, "*"}:
                    continue
                if _cancelled(cancellation):
                    # An observe-only hook records cancellation but cannot
                    # change control flow.  A fail-closed hook is the only
                    # policy allowed to block on that evidence.
                    policy = hook.failure_policy
                    issues.append(HookIssue(hook.name, event, "cancelled", policy == "fail_closed", 0.0, False, correlation_id, policy))
                    break
                started = time.monotonic()
                blocked = False
                error_text = ""
                unresolved = False
                try:
                    # Run untrusted callbacks in a bounded worker so a slow
                    # observer cannot stall the agent indefinitely.  A Python
                    # thread cannot be forcibly killed; after timeout it is
                    # detached and its result is ignored, while fail-closed
                    # callers still receive a blocking issue immediately.
                    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="forgecode-hook")
                    try:
                        callback_context = contextvars.copy_context()
                        future = executor.submit(callback_context.run, hook.callback, dict(safe_payload))
                        deadline = time.monotonic() + hook.timeout_seconds
                        while True:
                            if _cancelled(cancellation):
                                future.cancel()
                                unresolved = True
                                raise TimeoutError("hook cancelled")
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                future.cancel()
                                unresolved = True
                                raise TimeoutError("hook exceeded timeout")
                            try:
                                callback_result = future.result(timeout=min(0.05, remaining))
                                if hook.failure_policy == "fail_closed" and callback_result not in (None, True, False):
                                    raise TypeError("hook returned malformed output")
                                break
                            except FutureTimeoutError:
                                continue
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
                    issues.append(HookIssue(hook.name, event, error_text, blocked, round(elapsed, 6), unresolved, correlation_id, hook.failure_policy))
        finally:
            self._active.reset(token)
            self._record_history(event, correlation_id, payload, issues)
        return tuple(issues)


__all__ = ["Hook", "HookIssue", "HookRegistry"]
