"""The provider-neutral model -> tool -> result loop."""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import threading
import time
import math
from typing import Any, Callable

from ..models import CancellationToken, Message, ModelProvider, ProviderContext, ProviderError, is_valid_response
from ..context import RepositoryMapBuilder
from ..security.redaction import redact_text
from ..storage import Checkpoint, CheckpointStore, FileFingerprint, SessionStore, bounded
from ..tools import AgentMode, ToolContext, ToolRegistry
from ..tools import PauseRequested
from .context import ContextBuilder
from .lifecycle import LifecycleError, RunLifecycle, RunState
from .recovery import ContextCompactor
from .verification import VerificationResult


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int | None = None
    max_repeated_calls: int = 2
    verification_command: str | None = None
    max_verification_attempts: int = 2
    total_timeout_seconds: float = 600.0
    provider_timeout_seconds: float = 90.0
    max_tool_calls_per_turn: int = 256
    max_tool_calls_total: int = 512
    # A cancelled provider task gets a short chance to acknowledge
    # cancellation.  If it is still running afterwards it is detached and
    # the run is recovery-required; never wait indefinitely on untrusted code.
    provider_cleanup_grace_seconds: float = 0.1
    # Automatic rolling-context controls.  ``None`` derives a threshold from
    # the injected ContextBuilder budget.  Limits are hard per-run bounds.
    compact_threshold_chars: int | None = None
    max_auto_compactions: int = 8
    rolling_window_messages: int = 24


@dataclass(frozen=True)
class LoopResult:
    messages: tuple[Message, ...]
    stopped_reason: str
    error: str | None = None
    verification_ok: bool | None = None
    mode: str = AgentMode.ACT.value
    plan_summary: str | None = None
    explored: tuple[str, ...] = ()
    state: str = RunState.COMPLETED.value
    run_id: str | None = None
    audit_complete: bool = True
    verifications: tuple[VerificationResult, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.stopped_reason == "model_finished" and self.error is None


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        context: ToolContext,
        session: SessionStore | None = None,
        config: AgentConfig | None = None,
        context_builder: ContextBuilder | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ):
        self.provider = provider
        self.registry = registry
        self.session = session
        self.config = config or AgentConfig()
        self.context_builder = context_builder or ContextBuilder()
        self.on_event = on_event
        self.cancellation_token = cancellation_token or context.cancellation_token or CancellationToken()
        self.lifecycle = RunLifecycle()
        self.audit_complete = True
        self.run_id = session.run_id if session else None
        self.checkpoint_store = CheckpointStore(session.path.with_suffix(".checkpoint.json")) if session else None
        self._touched_paths: set[str] = set()
        self._last_tool_call: dict[str, Any] | None = None
        self._pending_actions: list[dict[str, Any]] = []
        self._approvals: list[dict[str, Any]] = []
        self._verification_snapshot: dict[str, Any] | None = None
        self._verification_results: list[VerificationResult] = []
        self._expected_fingerprints: dict[str, FileFingerprint] = {}
        self._last_event_sequence = 0
        self._last_context_summary = ""
        self._pause_requested = False
        self._interactive_pause = False
        self._pause_event_recorded = False
        # Attempt/retry ids are only unique within a provider request.  Some
        # adapters (and a number of test/fake adapters) restart their counter
        # for every turn, so using the id by itself can silently drop evidence
        # for a later request.  Keep the request id in the identity key.
        self._recorded_provider_attempts: set[tuple[str, str]] = set()
        self._recorded_provider_retries: set[tuple[str, str]] = set()
        self._provider_event_counter = 0
        self._last_provider_unresolved = False
        integer_limits = (self.config.max_repeated_calls, self.config.max_verification_attempts, self.config.max_tool_calls_per_turn, self.config.max_tool_calls_total, self.config.max_auto_compactions, self.config.rolling_window_messages)
        if self.config.max_steps is not None and (isinstance(self.config.max_steps, bool) or not isinstance(self.config.max_steps, int) or self.config.max_steps < 1):
            raise ValueError("max_steps must be positive when set")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in integer_limits):
            raise ValueError("loop limits must be positive")
        timeouts = (self.config.total_timeout_seconds, self.config.provider_timeout_seconds, self.config.provider_cleanup_grace_seconds)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in timeouts):
            raise ValueError("loop timeouts must be positive")
        if self.config.provider_cleanup_grace_seconds > 5:
            raise ValueError("provider_cleanup_grace_seconds must be at most 5 seconds")
        if self.config.compact_threshold_chars is not None and (isinstance(self.config.compact_threshold_chars, bool) or not isinstance(self.config.compact_threshold_chars, int) or self.config.compact_threshold_chars < 256):
            raise ValueError("compact_threshold_chars must be at least 256 or None")
        original_observer = context.approval_observer

        def record_approval(tool_name: str, arguments: dict[str, Any], approved: bool) -> None:
            if original_observer:
                original_observer(tool_name, arguments, approved)
            operation_id = arguments.get("transaction_id") if isinstance(arguments, dict) else None
            if tool_name == "apply_patch" and isinstance(arguments, dict):
                self._record("patch_preview", {"transaction_id": operation_id, "preview": self._bounded_arguments(arguments.get("patch", "")), "operations": arguments.get("operations", [])})
            if tool_name == "run_command" and isinstance(arguments, dict):
                self._record("command_start", {"command": self._bounded_arguments(arguments.get("command", "")), "risk": arguments.get("_risk"), "risk_reasons": arguments.get("_risk_reasons", [])})
            safe_arguments = self._bounded_arguments(arguments)
            self._record("approval_request", {"tool": tool_name, "arguments": safe_arguments})
            self._record("approval", {"tool": tool_name, "arguments": safe_arguments, "approved": approved})
            self._approvals.append({"tool": tool_name, "approved": approved})

        now = time.monotonic()
        configured_deadline = now + self.config.total_timeout_seconds
        if context.deadline_monotonic is not None:
            configured_deadline = min(configured_deadline, context.deadline_monotonic)
        self.context = ToolContext(context.guard, context.approval, approval_observer=record_approval, mode=context.mode, secrets=context.secrets, deadline_monotonic=configured_deadline, cancellation_requested=context.cancellation_requested, cancellation_token=self.cancellation_token, transaction_store=context.transaction_store, run_id=context.run_id or (session.run_id if session else ""), plan_id=context.plan_id, plan_item_id=context.plan_item_id, pre_side_effect_check=context.pre_side_effect_check, rules_fingerprint=context.rules_fingerprint, plan_fingerprint=context.plan_fingerprint, config_fingerprint=context.config_fingerprint, hooks=context.hooks, correlation_id=context.correlation_id, pause_wait=self._wait_for_side_effect_boundary)

    def _wait_for_side_effect_boundary(self) -> None:
        """Synchronously honor an interactive pause after approval.

        Tool execution is synchronous, so the gate intentionally blocks only
        at this safe boundary.  Cancellation remains observable while waiting
        and fails closed before transaction preparation or process spawn.
        """
        if not self._pause_requested:
            return
        if self._interactive_pause:
            if not self.lifecycle.terminal and self.lifecycle.state is not RunState.PAUSED:
                try:
                    self._transition(RunState.PAUSED, reason="interactive pause requested after approval")
                except LifecycleError:
                    self.lifecycle.state = RunState.PAUSED
            if not self._pause_event_recorded:
                self._record("pause", {"reason": "interactive pause requested after approval", "interactive": True})
                self._pause_event_recorded = True
            while self._pause_requested and not self.context.cancelled:
                time.sleep(0.01)
            self._pause_event_recorded = False
            if self.context.cancelled:
                return
            if self.lifecycle.state is RunState.PAUSED:
                try:
                    self._transition(RunState.DISCOVERING, reason="interactive pause released after approval")
                except LifecycleError:
                    self.lifecycle.state = RunState.DISCOVERING
            self._record("resume", {"reason": "interactive pause released after approval"})
        else:
            # Legacy/API pause still has to block the side effect.  The loop
            # observes this boundary error, records it as a tool result, and
            # returns its terminal ``paused`` result at the next loop edge.
            raise PauseRequested("operation paused before side effect")

    def _record(self, kind: str, payload: dict[str, Any]) -> None:
        safe_payload = self._sanitize_session_payload(payload)
        if self.session:
            try:
                event = self.session.append(kind, safe_payload, mode=self.context.mode.value)
                self._last_event_sequence = event.sequence
            except Exception as exc:  # audit I/O must not erase the task result
                self.audit_complete = False
                if self.on_event and kind != "session_error":
                    self.on_event(
                        "session_error",
                        {
                            "event": kind,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
        if self.on_event:
            self.on_event(kind, safe_payload)
        if kind != "session_error" and not self.audit_complete:
            # The task may continue safely, but a success claim is invalid
            # without a complete audit trail.
            pass

    def _sanitize_session_payload(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {str(item_key): self._sanitize_session_payload(item, str(item_key)) for item_key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_session_payload(item, key) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_session_payload(item, key) for item in value]
        if isinstance(value, str) and key in {"path", "cwd", "workspace", "root", "session"}:
            try:
                resolved = self.context.guard.resolve(value)
                return self.context.guard.relative(resolved)
            except (OSError, ValueError):
                return value[:500]
        return value

    def _checkpoint(self, *, reason: str) -> None:
        if self.checkpoint_store is None or self.run_id is None:
            return
        try:
            checkpoint = Checkpoint.create(
                self.context.guard,
                run_id=self.run_id,
                state=self.lifecycle.state.value,
                mode=self.context.mode.value,
                sequence=getattr(self, "_last_event_sequence", 0),
                files=tuple(sorted(self._touched_paths)),
                last_tool_call=self._last_tool_call,
                pending_actions=tuple(self._pending_actions),
                approvals=tuple(self._approvals),
                verification=self._verification_snapshot,
                context_summary=self._last_context_summary,
                rules_fingerprint=self.context.rules_fingerprint,
                plan_fingerprint=self.context.plan_fingerprint,
                config_fingerprint=self.context.config_fingerprint,
                secrets=self.context.secrets,
            )
            self.checkpoint_store.save(checkpoint)
            self._record("checkpoint", {"state": checkpoint.state, "sequence": checkpoint.sequence, "files": [item.path for item in checkpoint.files], "reason": reason})
        except Exception as exc:
            self.audit_complete = False
            if self.on_event:
                self.on_event("session_error", {"event": "checkpoint", "error": f"{type(exc).__name__}: {exc}"})

    def _capture_expected_fingerprints(self) -> None:
        for path in tuple(self._touched_paths):
            try:
                fingerprint = FileFingerprint.capture(self.context.guard, path)
                self._expected_fingerprints[path] = fingerprint
            except (OSError, ValueError):
                continue

    def _external_changes(self) -> tuple[str, ...]:
        changed: list[str] = []
        for path, expected in self._expected_fingerprints.items():
            try:
                if FileFingerprint.capture(self.context.guard, path) != expected:
                    changed.append(path)
            except (OSError, ValueError):
                changed.append(path)
        return tuple(sorted(changed))

    def pause(self) -> None:
        """Request a cooperative pause at the next provider/tool boundary."""
        self._pause_requested = True

    def enable_interactive_controls(self) -> None:
        """Keep the loop alive while paused so another thread can resume it."""
        self._interactive_pause = True

    def resume(self) -> bool:
        """Release an interactive pause; return whether a pause was pending."""
        pending = bool(self._pause_requested)
        self._pause_requested = False
        return pending

    async def _pause_at_boundary(self, messages: list[Message], verification_ok: bool | None, explored: list[str]) -> LoopResult | None:
        if not self._pause_requested:
            return None
        if not self._interactive_pause:
            if not self.lifecycle.terminal:
                try:
                    self._transition(RunState.PAUSED, reason="cooperative pause requested")
                except LifecycleError:
                    self.lifecycle.state = RunState.PAUSED
            result = LoopResult(tuple(messages), "paused", "run paused; resume requires checkpoint validation", verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
            self._record("pause", {"reason": "cooperative pause requested", "interactive": False})
            self._record("final", {"stopped_reason": result.stopped_reason, "state": result.state})
            return result
        if not self.lifecycle.terminal and self.lifecycle.state is not RunState.PAUSED:
            try:
                self._transition(RunState.PAUSED, reason="interactive pause requested")
            except LifecycleError:
                self.lifecycle.state = RunState.PAUSED
        if not self._pause_event_recorded:
            self._record("pause", {"reason": "interactive pause requested", "interactive": True})
            self._pause_event_recorded = True
        while self._pause_requested and not self.context.cancelled:
            await asyncio.sleep(0.01)
        self._pause_event_recorded = False
        if self.context.cancelled:
            return self._cancelled_result(messages, verification_ok, explored, reason=self.context.cancellation_reason)
        if self.lifecycle.state is RunState.PAUSED:
            try:
                self._transition(RunState.DISCOVERING, reason="interactive pause resumed")
            except LifecycleError:
                self.lifecycle.state = RunState.DISCOVERING
        self._record("resume", {"reason": "interactive pause released"})
        return None

    def cancel(self, reason: str = "cancelled") -> bool:
        """Request cooperative cancellation of this run.

        The token is safe to signal from another thread (for example a CLI
        interrupt handler).  Blocking tools poll the same token and the loop
        observes it at provider/tool boundaries; an in-flight provider is
        given a bounded context so it can stop assembling transport data.
        """
        return self.cancellation_token.cancel(reason)

    def _provider_context(self, step: int) -> ProviderContext:
        return ProviderContext(
            deadline_monotonic=self.context.deadline_monotonic,
            cancellation_token=self.cancellation_token,
            cancellation_requested=self.context.cancellation_requested,
            request_id=f"{self.run_id or 'run'}:{step}",
            on_text_delta=lambda text: self._record("model_delta", {"step": step, "content": text[:2_000]}),
        )

    async def _complete_provider(self, messages: list[Message], tools: list[dict[str, Any]], provider_context: ProviderContext) -> Any:
        """Call old two-argument providers and new context-aware adapters."""
        completer = self.provider.complete
        signature = None
        try:
            signature = inspect.signature(completer)
            parameters = tuple(signature.parameters.values())
        except (TypeError, ValueError):
            parameters = ()
        if signature is not None and "context" in signature.parameters:
            return await completer(messages, tools, context=provider_context)
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return await completer(messages, tools, context=provider_context)
        if any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters) or len(parameters) >= 3:
            return await completer(messages, tools, provider_context)
        return await completer(messages, tools)

    @staticmethod
    def _consume_provider_future(future: concurrent.futures.Future[Any]) -> None:
        """Consume a late provider exception after a bounded return."""
        try:
            future.exception()
        except BaseException:
            return

    def _start_provider_worker(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        provider_context: ProviderContext,
    ) -> tuple[concurrent.futures.Future[Any], threading.Thread, Callable[[], None]]:
        """Run the provider coroutine on a daemon event-loop worker.

        An asyncio task on the caller's loop is not a hard cancellation
        boundary: a coroutine may catch ``CancelledError`` and keep running,
        causing ``asyncio.run`` (and some service shutdowns) to wait for it.
        Isolating the untrusted provider loop in a daemon thread lets the
        caller return at its deadline while late results remain detached and
        are never dispatched to tools. Context-aware providers still receive
        the shared token/deadline and can terminate cooperatively.
        """
        result: concurrent.futures.Future[Any] = concurrent.futures.Future()
        state: dict[str, Any] = {"loop": None, "task": None}
        ready = threading.Event()
        cancel_requested = threading.Event()

        def worker() -> None:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                coroutine = self._complete_provider(messages, tools, provider_context)
                task = loop.create_task(coroutine)
                state["loop"] = loop
                state["task"] = task
                ready.set()
                if cancel_requested.is_set():
                    task.cancel()
                try:
                    value = loop.run_until_complete(task)
                except BaseException as exc:
                    # Provider code is untrusted and may (accidentally or
                    # deliberately) raise a process-level BaseException such
                    # as SystemExit.  Never let that escape the worker and
                    # terminate the host process.  Preserve asyncio
                    # cancellation so the caller can map it to its stable
                    # cancelled result; normalize every other BaseException
                    # to the ordinary provider error boundary.
                    if isinstance(exc, asyncio.CancelledError):
                        safe_exc: BaseException = exc
                    elif isinstance(exc, KeyboardInterrupt):
                        safe_exc = ProviderError("provider worker interrupted", category="cancelled", retryable=False)
                    elif isinstance(exc, Exception):
                        # Preserve ordinary provider exception categories and
                        # diagnostics; only process-level BaseExceptions need
                        # normalization at this isolation boundary.
                        safe_exc = exc
                    else:
                        safe_exc = ProviderError("provider worker failed", category="provider_error", retryable=False)
                    # Mark the task exception as retrieved before closing the
                    # isolated loop; otherwise asyncio emits an unbounded
                    # ``Task exception was never retrieved`` diagnostic for
                    # provider-raised SystemExit/KeyboardInterrupt.
                    try:
                        task.exception()
                    except BaseException:
                        pass
                    try:
                        result.set_exception(safe_exc)
                    except (concurrent.futures.InvalidStateError, RuntimeError):
                        pass
                else:
                    try:
                        result.set_result(value)
                    except (concurrent.futures.InvalidStateError, RuntimeError):
                        pass
            finally:
                # Do not run a potentially unbounded provider cleanup phase.
                # Cancelling pending tasks before close avoids retaining loop
                # references, while the daemon thread remains a safe fallback
                # for providers that ignore cancellation entirely.
                try:
                    for pending in asyncio.all_tasks(loop):
                        pending.cancel()
                except Exception:
                    pass
                asyncio.set_event_loop(None)
                loop.close()

        def request_cancel() -> None:
            """Ask the isolated provider task to stop, without blocking."""
            cancel_requested.set()
            loop = state.get("loop")
            task = state.get("task")
            if loop is not None and task is not None and not task.done():
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass

        thread = threading.Thread(target=worker, name="forgecode-provider", daemon=True)
        thread.start()
        return result, thread, request_cancel

    async def _stop_provider_worker(self, future: concurrent.futures.Future[Any], thread: threading.Thread, request_cancel: Callable[[], None]) -> bool:
        """Wait a bounded grace and report whether the daemon worker stopped."""
        request_cancel()
        deadline = time.monotonic() + self.config.provider_cleanup_grace_seconds
        while not future.done() and thread.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        stopped = not thread.is_alive()
        if future.done():
            self._consume_provider_future(future)
        else:
            # A future may complete just after the thread check; retaining the
            # result is harmless and lets the worker publish without touching
            # the caller's event loop. The caller treats this attempt as
            # unresolved unless termination was observed.
            future.add_done_callback(self._consume_provider_future)
        return stopped

    def _record_provider_events(
        self,
        *,
        unresolved_request_id: str | None = None,
        error_category: str | None = None,
        outcome: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        """Persist provider/retry evidence once, including detached attempts.

        Provider adapters expose diagnostic lists for compatibility.  A list
        can persist across loop turns (and a detached task can mutate an item
        after this method returns), so snapshot each identity once.  Duplicate
        identities are not emitted repeatedly; a synthetic unresolved attempt
        fills the evidence gap when a legacy provider exposes no list at all.
        """
        attempts = getattr(self.provider, "attempt_events", ())
        seen_request = False
        if isinstance(attempts, (list, tuple)):
            for raw in attempts:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                request_id = item.get("request_id")
                attempt_id = item.get("attempt_id")
                request_key = str(request_id or "")
                attempt_key = str(attempt_id or "")
                if not attempt_key:
                    attempt_key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)[:2_000]
                if unresolved_request_id and request_id == unresolved_request_id:
                    seen_request = True
                    # An in-flight adapter item may not have had a terminal
                    # outcome before the loop detached it.  Snapshot it as an
                    # unresolved cancellation.  A detached adapter can race
                    # this snapshot and publish a late success, so an
                    # unresolved request always wins over the adapter's
                    # terminal marker.
                    item["outcome"] = outcome or "unresolved"
                    item["error_category"] = error_category or "unresolved"
                    item["unresolved"] = True
                    if retryable is not None:
                        item["retryable"] = bool(retryable)
                key = (request_key, attempt_key)
                if key in self._recorded_provider_attempts:
                    continue
                self._recorded_provider_attempts.add(key)
                self._record("provider_attempt", item)

        retries = getattr(self.provider, "retry_events", ())
        if isinstance(retries, (list, tuple)):
            for raw in retries:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                request_key = str(item.get("request_id") or "")
                attempt_key = str(item.get("attempt_id") or "")
                if not attempt_key:
                    attempt_key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)[:2_000]
                key = (request_key, attempt_key)
                if key in self._recorded_provider_retries:
                    continue
                self._recorded_provider_retries.add(key)
                self._record("provider_retry", item)

        if unresolved_request_id and not seen_request:
            # Legacy providers have no attempt list.  Record an explicit,
            # unique unresolved attempt so recovery can distinguish timeout
            # from a clean provider failure.
            self._provider_event_counter += 1
            now = datetime.now(timezone.utc).isoformat()
            synthetic_id = f"{unresolved_request_id}:unresolved:{self._provider_event_counter}"
            self._record(
                "provider_attempt",
                {
                    "request_id": unresolved_request_id,
                    "attempt_id": synthetic_id,
                    "attempt": self._provider_event_counter,
                    "protocol": "unknown",
                    "started_at": now,
                    "ended_at": now,
                    "duration_seconds": 0.0,
                    "outcome": outcome or "unresolved",
                    "error_category": error_category or "unresolved",
                    "retryable": bool(retryable) if retryable is not None else False,
                    "unresolved": True,
                },
            )

    async def _await_provider(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        provider_context: ProviderContext,
        timeout: float,
    ) -> Any:
        """Await a provider while polling cancellation and absolute deadline.

        Legacy providers may expose only the two-argument API and may not poll
        the token themselves.  Their task is cancelled and detached after a
        bounded grace period; no response from that task is ever dispatched
        to tools.  Context-aware adapters receive the same token and can stop
        their transport cooperatively.
        """
        future, worker, request_cancel = self._start_provider_worker(messages, tools, provider_context)
        deadline = time.monotonic() + timeout
        self._last_provider_unresolved = False
        try:
            while True:
                if self.context.cancelled:
                    stopped = await self._stop_provider_worker(future, worker, request_cancel)
                    self._last_provider_unresolved = not stopped
                    raise ProviderError("provider request cancelled", category="cancelled", retryable=False, request_id=provider_context.request_id, unresolved=not stopped)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stopped = await self._stop_provider_worker(future, worker, request_cancel)
                    self._last_provider_unresolved = not stopped
                    raise ProviderError("provider request deadline exceeded", category="deadline_exceeded", retryable=False, request_id=provider_context.request_id, unresolved=not stopped)
                if future.done():
                    return future.result()
                await asyncio.sleep(min(0.01, remaining))
        except asyncio.CancelledError:
            stopped = await self._stop_provider_worker(future, worker, request_cancel)
            self._last_provider_unresolved = not stopped
            raise

    def _cancelled_result(self, messages: list[Message], verification_ok: bool | None, explored: list[str], *, reason: str | None = None, category: str = "cancelled") -> LoopResult:
        safe_reason = redact_text(str(reason or self.cancellation_token.reason or "run cancelled")[:1_000], self.context.secrets)
        # A detached provider worker is evidence for the audit trail, but it
        # does not by itself create an unsafe filesystem/tool side effect.
        # Explicit cancellation should therefore expose the stable
        # ``cancelled`` terminal state for ordinary in-flight model requests.
        # Escalate to recovery only when an unresolved worker coincides with a
        # pending side-effecting action that requires reconciliation.
        unresolved = bool(self._last_provider_unresolved) and bool(self._pending_actions)
        if not self.lifecycle.terminal:
            try:
                # User cancellation remains the stable public ``cancelled``
                # state for compatibility.  If the provider worker could not
                # be stopped, the event carries ``unresolved`` evidence and
                # recovery can inspect it; it must never be reported as a
                # successful run or replayed automatically.
                self._transition(RunState.RECOVERY_REQUIRED if unresolved else RunState.CANCELLED, reason=safe_reason)
            except LifecycleError:
                self.lifecycle.state = RunState.RECOVERY_REQUIRED if unresolved else RunState.CANCELLED
        self._record("cancellation", {"category": category, "reason": safe_reason, "pending_actions": list(self._pending_actions), "unresolved": unresolved})
        result = LoopResult(tuple(messages), category, safe_reason, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
        self._record("final", {"stopped_reason": result.stopped_reason, "error": result.error, "state": result.state, "pending_actions": list(self._pending_actions)})
        return result

    def _transition(self, target: RunState, *, reason: str | None = None) -> None:
        previous, current = self.lifecycle.transition(target)
        self._record(
            "state_transition",
            {
                "from": previous.value,
                "to": current.value,
                "reason": reason or "unspecified",
            },
        )
        self._checkpoint(reason=f"state:{current.value}")

    def _fail_state(self, reason: str) -> None:
        if not self.lifecycle.terminal:
            try:
                self._transition(RunState.FAILED, reason=reason)
            except LifecycleError:
                # A defensive fallback for an unexpected internal state must
                # never hide the original user-visible failure.
                self.lifecycle.state = RunState.FAILED

    @staticmethod
    def _bounded_arguments(arguments: Any, limit: int = 4_000) -> Any:
        return bounded(arguments, max_string_chars=limit)

    @staticmethod
    def _context_chars(messages: list[Message]) -> int:
        """Estimate serialized provider context size, including tool calls."""
        return sum(len(message.content) + len(json.dumps([
            {"id": call.id, "name": call.name, "arguments": bounded(call.arguments, max_string_chars=4_000)}
            for call in message.tool_calls[:64]
        ], ensure_ascii=False, separators=(",", ":"), default=str)) for message in messages)

    def _maybe_auto_compact(self, messages: list[Message], *, step: int) -> list[Message]:
        """Compact an oversized live history once per source sequence.

        ContextBuilder.fit() remains the final hard budget, but this method
        records why older context was evicted and carries an evidence-only
        summary into the next request.  No session bytes are rewritten.
        """
        budget = max(256, int(self.context_builder.max_chars))
        threshold = self.config.compact_threshold_chars
        if threshold is None:
            threshold = max(256, int(budget * 0.8))
        threshold = min(budget, threshold)
        current_size = self._context_chars(messages)
        if current_size < threshold:
            self._compaction_hysteresis = False
            return messages
        if self._compaction_hysteresis:
            return messages
        if self._auto_compactions >= self.config.max_auto_compactions:
            self._record("context_compaction_skipped", {"reason": "limit", "step": step, "context_chars": current_size, "threshold_chars": threshold})
            self._compaction_hysteresis = True
            return messages
        if self.session is None:
            # A non-durable caller still receives bounded rolling context, but
            # there is no append-only stream in which to record a summary.
            fitted = self.context_builder.fit(messages)
            self._compaction_hysteresis = True
            return fitted
        try:
            read_result = self.session.read_with_issues()
            if read_result.issues:
                raise ValueError("session contains validation issues")
            events = read_result.events
            source_end = max((event.sequence for event in events), default=0)
            if source_end and source_end == self._last_compaction_sequence:
                self._compaction_hysteresis = True
                return messages
            max_summary = max(512, min(24_000, int(budget * 0.45)))
            result = ContextCompactor(max_chars=max_summary, recent_events=self.config.rolling_window_messages).compact_events(
                events,
                current_messages=messages,
                reason="automatic",
            )
            payload = result.to_dict()
            payload.update({"step": step, "trigger_context_chars": current_size, "threshold_chars": threshold, "budget_chars": budget})
            self._record("context_compacted", payload)
            self._auto_compactions += 1
            self._last_compaction_sequence = source_end
            summary = Message(
                role="system",
                content=(
                    "AUTOMATIC CONTEXT SUMMARY (evidence only; source sequence "
                    f"{result.source_sequence_start}-{result.source_sequence_end}, "
                    f"fingerprint={result.context_fingerprint}):\n{result.summary}"
                )[:max_summary + 512],
            )
            system = next((message for message in messages if message.role == "system"), None)
            user = next((message for message in messages if message.role == "user"), None)
            tail = list(messages[-self.config.rolling_window_messages:])
            # A tool result without its assistant tool-call pairing is not
            # valid provider context.  Include the immediate predecessor when
            # the rolling cut starts in the middle of a pair.
            if tail and tail[0].role == "tool":
                index = messages.index(tail[0])
                if index > 0:
                    tail.insert(0, messages[index - 1])
            compacted: list[Message] = []
            if system is not None:
                compacted.append(system)
            compacted.append(summary)
            if user is not None and user not in compacted and user not in tail:
                compacted.append(user)
            compacted.extend(message for message in tail if message not in compacted)
            messages[:] = self.context_builder.fit(compacted)
            self._compaction_hysteresis = True
            return messages
        except Exception as exc:
            self._record("context_compaction_error", {"step": step, "context_chars": current_size, "error": f"{type(exc).__name__}: {exc}"})
            self._compaction_hysteresis = True
            return self.context_builder.fit(messages)

    async def run(self, prompt: str) -> LoopResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        prompt = redact_text(prompt, self.context.secrets)
        messages: list[Message] = [
            self.context_builder.system_message(
                self.context.guard.root,
                tuple(definition.name for definition in self.registry.definitions(self.context.mode)),
                approval_mode="interactive or explicit auto-approve",
                mode=self.context.mode.value,
            ),
            Message(role="user", content=prompt),
        ]
        # Build a bounded read-only map before the first provider turn. The
        # map is advisory context; tools still re-read exact files before any
        # side effect and the snapshot never authorizes a write.
        try:
            repository_map = RepositoryMapBuilder(self.context.guard, max_files=500).build()
            map_plan = repository_map.plan_context(prompt, budget_chars=8_000)
            messages.insert(1, Message(role="system", content=("Bounded repository map (advisory; inspect exact files before editing):\n" + map_plan.render())[:8_000]))
            self._record("repository_snapshot", {"files": len(repository_map.snapshot.files), "omitted": repository_map.snapshot.omitted, "errors": len(repository_map.snapshot.errors), "selected_paths": list(map_plan.selected_paths), "context_omitted": map_plan.omitted})
        except (OSError, ValueError) as exc:
            self._record("repository_snapshot_error", {"error": f"{type(exc).__name__}: {exc}"})
        self._record("run_created", {"run_id": self.run_id, "mode": self.context.mode.value})
        self._auto_compactions = 0
        self._last_compaction_sequence = 0
        self._compaction_hysteresis = False
        self._record("run_started", {"run_id": self.run_id, "mode": self.context.mode.value})
        self._transition(RunState.DISCOVERING, reason="task accepted")
        self._record("mode", {"mode": self.context.mode.value, "side_effects_allowed": self.context.mode in {AgentMode.ACT, AgentMode.BYPASS}})
        self._record("user_message", {"content": prompt, "mode": self.context.mode.value})
        self._last_context_summary = prompt[:8_000]
        seen_calls: dict[str, int] = {}
        verification_runs = 0
        verification_ok: bool | None = None
        explored: list[str] = []
        total_tool_calls = 0

        step = 0
        while self.config.max_steps is None or step < self.config.max_steps:
            step += 1
            if self.context.cancelled:
                return self._cancelled_result(messages, verification_ok, explored, reason=self.context.cancellation_reason)
            if self.context.deadline_monotonic is not None and self.context.deadline_monotonic <= time.monotonic():
                error_text = "run deadline exceeded before next agent step"
                self._fail_state("run deadline")
                self._record("error", {"category": "deadline_exceeded", "message": error_text})
                result = LoopResult(tuple(messages), "deadline_exceeded", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": result.error, "state": result.state})
                return result
            paused_result = await self._pause_at_boundary(messages, verification_ok, explored)
            if paused_result is not None:
                return paused_result
            request_messages = self._maybe_auto_compact(messages, step=step)
            request_messages = self.context_builder.fit(request_messages)
            self._last_context_summary = "\n".join(message.content for message in request_messages[-8:])[:8_000]
            self._record("model_progress", {"step": step, "message": "Analyzing the task and deciding the next safe action…" if step == 0 else "Reviewing the latest tool result and continuing…"})
            self._record("model_request", {"step": step, "message_count": len(request_messages), "context_chars": sum(len(message.content) for message in request_messages), "tool_count": len(self.registry.schemas(self.context.mode))})
            provider_started = time.monotonic()
            try:
                if self.context.hooks is not None:
                    hook_issues = self.context.hooks.emit("before_model", {"step": step, "message_count": len(request_messages), "tool_count": len(self.registry.schemas(self.context.mode)), "run_id": self.run_id, "request_id": f"{self.run_id or 'run'}:{step}"}, cancellation=self.cancellation_token or self.context.cancellation_requested)
                    if any(issue.blocked for issue in hook_issues):
                        raise ProviderError("model request blocked by lifecycle hook", category="hook_blocked")
                remaining = self.context.remaining_seconds(self.config.provider_timeout_seconds)
                if remaining <= 0:
                    raise ProviderError("run deadline exceeded before provider request", category="deadline_exceeded")
                response = await self._await_provider(
                    request_messages,
                    self.registry.schemas(self.context.mode),
                    self._provider_context(step),
                    remaining,
                )
                if self.context.hooks is not None:
                    hook_issues = self.context.hooks.emit("after_model", {"step": step, "finish_reason": getattr(response, "finish_reason", None), "tool_calls": len(getattr(getattr(response, "message", None), "tool_calls", ())), "run_id": self.run_id, "request_id": f"{self.run_id or 'run'}:{step}"}, cancellation=self.cancellation_token or self.context.cancellation_requested)
                    if any(issue.blocked for issue in hook_issues):
                        raise ProviderError("model response blocked by lifecycle hook", category="hook_blocked")
                self._record_provider_events()
            except (KeyboardInterrupt, asyncio.CancelledError):
                error_text = "agent interrupted by user"
                self.cancellation_token.cancel("user interruption")
                self._record_provider_events(
                    unresolved_request_id=f"{self.run_id or 'run'}:{step}",
                    error_category="cancelled",
                    outcome="interrupted",
                )
                return self._cancelled_result(messages, verification_ok, explored, reason=error_text, category="interrupted")
            except ProviderError as exc:
                error_text = str(exc)
                self._record_provider_events(
                    unresolved_request_id=exc.request_id if exc.unresolved else None,
                    error_category=exc.category,
                    outcome="unresolved" if exc.unresolved else "error",
                    retryable=exc.retryable,
                )
                if exc.category == "cancelled" or self.context.cancelled:
                    return self._cancelled_result(messages, verification_ok, explored, reason=error_text, category="cancelled")
                if exc.category == "deadline_exceeded":
                    if exc.unresolved:
                        if not self.lifecycle.terminal:
                            try:
                                self._transition(RunState.RECOVERY_REQUIRED, reason="provider worker unresolved after deadline")
                            except LifecycleError:
                                self.lifecycle.state = RunState.RECOVERY_REQUIRED
                    else:
                        self._fail_state("provider deadline")
                    self._record("error", {"category": exc.category, "message": error_text})
                    result = LoopResult(tuple(messages), "deadline_exceeded", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                    self._record("final", {"stopped_reason": result.stopped_reason, "error": result.error, "state": result.state})
                    return result
                self._fail_state("provider error")
                self._record("error", {"category": exc.category, "message": error_text})
                result = LoopResult(tuple(messages), "provider_error", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result
            except asyncio.TimeoutError:
                error_text = "model request exceeded the run/provider deadline"
                if self._last_provider_unresolved:
                    if not self.lifecycle.terminal:
                        try:
                            self._transition(RunState.RECOVERY_REQUIRED, reason="provider worker unresolved after deadline")
                        except LifecycleError:
                            self.lifecycle.state = RunState.RECOVERY_REQUIRED
                else:
                    self._fail_state("provider deadline")
                self._record_provider_events(
                    unresolved_request_id=f"{self.run_id or 'run'}:{step}" if self._last_provider_unresolved else None,
                    error_category="deadline_exceeded",
                    outcome="unresolved" if self._last_provider_unresolved else "error",
                )
                self._record("error", {"category": "deadline_exceeded", "message": error_text})
                result = LoopResult(tuple(messages), "deadline_exceeded", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                self._fail_state("unexpected provider error")
                self._record("error", {"category": "unexpected_provider_error", "message": error_text})
                result = LoopResult(tuple(messages), "provider_error", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result

            if not is_valid_response(response):
                error_text = "model returned an invalid response"
                self._fail_state("invalid response")
                self._record("error", {"category": "invalid_response", "message": error_text})
                result = LoopResult(tuple(messages), "invalid_response", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result

            # Cancellation may arrive while the provider is assembling its
            # response. Do not even enqueue model tool calls after that point:
            # this closes the provider-return/side-effect execution race and
            # keeps a cancelled response from entering approval or journaling.
            if self.context.cancelled:
                return self._cancelled_result(messages, verification_ok, explored, reason=self.context.cancellation_reason)

            # A pause requested while the provider was in flight must take
            # effect before the response is appended or any tool call is
            # considered.  The response remains local and is resumed through
            # the same loop; no model/tool state is duplicated.
            paused_result = await self._pause_at_boundary(messages, verification_ok, explored)
            if paused_result is not None:
                return paused_result

            if not response.message.content and not response.message.tool_calls:
                error_text = "model returned an empty response"
                self._fail_state("empty response")
                self._record("error", {"category": "empty_response", "message": error_text})
                result = LoopResult(tuple(messages), "empty_response", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result

            messages.append(response.message)
            duplicate_ids = len({call.id for call in response.message.tool_calls}) != len(response.message.tool_calls)
            total_tool_calls += len(response.message.tool_calls)
            if duplicate_ids or len(response.message.tool_calls) > self.config.max_tool_calls_per_turn or total_tool_calls > self.config.max_tool_calls_total:
                error_text = "model response exceeded tool-call limits or repeated a tool_call_id"
                self._fail_state("tool call protocol violation")
                self._record("error", {"category": "tool_call_limit", "message": error_text, "turn_calls": len(response.message.tool_calls), "total_calls": total_tool_calls, "duplicate_ids": duplicate_ids})
                result = LoopResult(tuple(messages), "tool_call_limit", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result
            self._record("model_message", {"step": step, "content": response.message.content[:16_000], "tool_calls": [call.name for call in response.message.tool_calls], "finish_reason": response.finish_reason, "usage": response.usage, "duration_seconds": round(time.monotonic() - provider_started, 3)})
            if not response.message.tool_calls:
                if self.context.mode is AgentMode.PLAN:
                    if self.lifecycle.state is RunState.DISCOVERING:
                        self._transition(RunState.PLANNING, reason="model produced plan")
                    self._transition(RunState.COMPLETED, reason="plan returned")
                    plan_summary = response.message.content
                    self._record("plan_summary", {"content": plan_summary[:16_000], "explored": explored})
                    if self.config.verification_command:
                        self._record("verification_skipped", {"reason": "plan_mode", "command": self._bounded_arguments(self.config.verification_command)})
                    result = LoopResult(
                        tuple(messages),
                        "model_finished",
                        verification_ok=None,
                        mode=self.context.mode.value,
                        plan_summary=plan_summary,
                        explored=tuple(explored),
                        state=self.lifecycle.state.value,
                        run_id=self.run_id,
                        audit_complete=self.audit_complete,
                        verifications=tuple(self._verification_results),
                    )
                    self._record("final", {"stopped_reason": result.stopped_reason, "mode": self.context.mode.value, "verification_ok": None})
                    result = self._with_current_audit(result)
                    return result
                if self.config.verification_command and verification_ok is not True and verification_runs < self.config.max_verification_attempts:
                    paused_result = await self._pause_at_boundary(messages, verification_ok, explored)
                    if paused_result is not None:
                        return paused_result
                    verification_runs += 1
                    if self.lifecycle.state is RunState.DISCOVERING:
                        self._transition(RunState.PLANNING, reason="verification requested")
                    if self.lifecycle.state is RunState.PLANNING:
                        self._transition(RunState.VERIFYING, reason="verification started")
                    verification_arguments = {"command": self.config.verification_command, "timeout_seconds": 120}
                    self._record("verification_start", {"attempt": verification_runs, "command": self._bounded_arguments(self.config.verification_command)})
                    verification = self.registry.execute("run_command", verification_arguments, self.context)
                    verification_ok = verification.ok
                    external_changes = self._external_changes()
                    if external_changes:
                        verification_ok = False
                    verification_result = VerificationResult(
                        command=self.config.verification_command,
                        attempt=verification_runs,
                        risk=verification.metadata.get("risk"),
                        approval=verification.metadata.get("approval"),
                        exit_code=verification.metadata.get("exit_code"),
                        timed_out=bool(verification.metadata.get("timed_out", False)),
                        duration_seconds=verification.metadata.get("duration_seconds"),
                        stdout=str(verification.metadata.get("stdout", ""))[:20_000],
                        stderr=str(verification.metadata.get("stderr", ""))[:20_000],
                        failure_summary=None if verification.ok and not external_changes else ("external file changed during verification" if external_changes else verification.output[:2_000]),
                        changed_files=external_changes,
                        next_action="repair" if not verification_ok and verification_runs < self.config.max_verification_attempts else ("conflict" if external_changes else "complete"),
                        conflict=bool(external_changes),
                        ok=bool(verification_ok),
                    )
                    self._verification_results.append(verification_result)
                    self._verification_snapshot = verification_result.to_dict()
                    if self.context.transaction_store is not None:
                        try:
                            latest_transaction = next((manifest for manifest in self.context.transaction_store.list(limit=20) if manifest.run_id == self.context.run_id and manifest.state == "committed"), None)
                            if latest_transaction is not None:
                                self.context.transaction_store.attach_verification(latest_transaction.transaction_id, verification_result.to_dict())
                                self._record("transaction_verification", {"transaction_id": latest_transaction.transaction_id, "verification": verification_result.to_dict()})
                        except Exception as exc:
                            self.audit_complete = False
                            self._record("transaction_error", {"error": type(exc).__name__, "operation": "attach_verification"})
                    self._record("verification_result", {"attempt": verification_runs, "ok": verification_ok, "result": verification_result.to_dict(), "output": verification.output[:20_000], "metadata": self._bounded_arguments(verification.metadata)})
                    messages.append(Message(role="user", content=f"Verification result for `{self.config.verification_command}`:\n{verification.output}"))
                    if not verification.ok and verification_runs >= self.config.max_verification_attempts:
                        error_text = "verification command failed after the configured attempts"
                        self._fail_state("verification failed")
                        result = LoopResult(tuple(messages), "verification_failed", error_text, False, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
                        self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text, "verification_ok": False})
                        result = self._with_current_audit(result)
                        return result
                    continue
                if verification_ok is False:
                    error_text = "verification did not pass"
                    self._fail_state("verification failed")
                    result = LoopResult(tuple(messages), "verification_failed", error_text, False, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
                    self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text, "verification_ok": False})
                    result = self._with_current_audit(result)
                    return result
                if self.lifecycle.state is RunState.DISCOVERING:
                    self._transition(RunState.PLANNING, reason="model finished")
                if self.lifecycle.state is not RunState.COMPLETED:
                    self._transition(RunState.COMPLETED, reason="model finished")
                result = LoopResult(tuple(messages), "model_finished", verification_ok=verification_ok, mode=self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
                self._record("final", {"stopped_reason": result.stopped_reason, "verification_ok": verification_ok})
                result = self._with_current_audit(result)
                return result

            for call in response.message.tool_calls:
                paused_result = await self._pause_at_boundary(messages, verification_ok, explored)
                if paused_result is not None:
                    return paused_result
                fingerprint_source = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, default=str)
                fingerprint = hashlib.sha256(f"{call.name}:{fingerprint_source}".encode("utf-8", errors="replace")).hexdigest()
                seen_calls[fingerprint] = seen_calls.get(fingerprint, 0) + 1
                self._record("tool_call", {"step": step, "id": call.id, "tool": call.name, "arguments": self._bounded_arguments(call.arguments)})
                self._last_tool_call = {"id": call.id, "tool": call.name, "arguments": self._bounded_arguments(call.arguments), "step": step}
                path_argument = call.arguments.get("path") if isinstance(call.arguments, dict) else None
                if isinstance(path_argument, str) and call.name in {"read_file", "write_file", "apply_patch"}:
                    self._touched_paths.add(path_argument)
                if seen_calls[fingerprint] > self.config.max_repeated_calls:
                    output = "repeated identical tool call limit exceeded"
                    self._record("error", {"category": "repeated_tool_call", "message": output, "tool": call.name})
                    messages.append(Message(role="tool", content=output, tool_call_id=call.id))
                    self._fail_state("repeated tool call")
                    result = LoopResult(tuple(messages), "repeated_tool_call", output, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                    self._record("final", {"stopped_reason": result.stopped_reason, "error": output})
                    return result
                side_effecting = call.name in {definition.name for definition in self.registry.definitions() if definition.side_effecting}
                if side_effecting and self.context.mode in {AgentMode.ACT, AgentMode.BYPASS}:
                    if self.lifecycle.state is RunState.DISCOVERING:
                        self._transition(RunState.PLANNING, reason="side effect proposed")
                    if self.lifecycle.state is RunState.VERIFYING:
                        self._transition(RunState.AWAITING_APPROVAL, reason="repair side effect proposed")
                    if self.lifecycle.state is RunState.PLANNING:
                        self._transition(RunState.AWAITING_APPROVAL, reason="approval required")
                    if self.lifecycle.state is RunState.AWAITING_APPROVAL:
                        self._transition(RunState.ACTING, reason="side effect execution")
                if side_effecting:
                    self._pending_actions.append({"id": call.id, "tool": call.name, "arguments": self._bounded_arguments(call.arguments)})
                    # Journal the pending action before entering an
                    # untrusted side-effecting tool. If the process crashes or
                    # cancellation races the call, recovery can expose the
                    # unresolved action instead of replaying it.
                    self._checkpoint(reason=f"pending:{call.name}")
                    if self.context.cancelled:
                        return self._cancelled_result(messages, verification_ok, explored, reason=self.context.cancellation_reason)
                tool_started = time.monotonic()
                tool_result = self.registry.execute(call.name, call.arguments, self.context)
                tool_duration = round(time.monotonic() - tool_started, 3)
                if isinstance(tool_result.metadata, dict) and tool_result.metadata.get("error") == "paused" and not self._interactive_pause:
                    # The non-interactive API keeps its historical terminal
                    # pause result.  Do not continue to a provider turn after
                    # a side-effect boundary rejected execution.
                    paused_result = await self._pause_at_boundary(messages, verification_ok, explored)
                    if paused_result is not None:
                        return paused_result
                unresolved = False
                if side_effecting:
                    metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
                    unresolved = bool(
                        metadata.get("termination_result") == "unresolved"
                        or metadata.get("unresolved") is True
                        or (metadata.get("recovery_required") is True)
                        or (metadata.get("error") in {"transaction_commit_failed", "transaction_prepare_failed"} and metadata.get("rolled_back") is not True)
                    )
                    if unresolved:
                        # Keep the ledger entry until recovery explicitly
                        # resolves it.  Removing it here would make a
                        # detached process/partial transaction look complete.
                        self._record("pending_action_unresolved", {"id": call.id, "tool": call.name, "metadata": self._bounded_arguments(metadata)})
                    else:
                        self._pending_actions = [item for item in self._pending_actions if item.get("id") != call.id]
                if call.name in {"list_files", "read_file", "search", "workspace_summary"}:
                    detail = str(tool_result.metadata.get("path") or call.arguments.get("path") or call.arguments.get("pattern") or call.name)
                    explored.append(f"{call.name}:{detail}"[:500])
                if tool_result.metadata.get("error") == "mode_denied":
                    self._record(
                        "mode_denied",
                        {
                            "step": step,
                            "id": call.id,
                            "tool": call.name,
                            "mode": self.context.mode.value,
                            "reason": tool_result.output[:1_000],
                        },
                    )
                self._record("tool_result", {"step": step, "id": call.id, "tool": call.name, "ok": tool_result.ok, "output": tool_result.output[:20_000], "metadata": self._bounded_arguments(tool_result.metadata), "duration_seconds": tool_duration, "output_chars": len(tool_result.output)})
                for changed_path in tool_result.metadata.get("paths", []) if isinstance(tool_result.metadata, dict) else ():
                    if isinstance(changed_path, str):
                        self._touched_paths.add(changed_path)
                if isinstance(tool_result.metadata, dict) and isinstance(tool_result.metadata.get("path"), str):
                    self._touched_paths.add(tool_result.metadata["path"])
                if side_effecting and tool_result.ok:
                    self._capture_expected_fingerprints()
                if call.name == "apply_patch" and tool_result.metadata.get("transaction_id"):
                    patch_event = "patch_commit" if tool_result.ok else ("patch_rollback" if tool_result.metadata.get("rolled_back") else "patch_refused")
                    self._record(patch_event, {"transaction_id": tool_result.metadata.get("transaction_id"), "ok": tool_result.ok, "error": tool_result.metadata.get("error"), "operations": tool_result.metadata.get("operations", [])})
                    if tool_result.ok:
                        self._record("transaction_committed", {"transaction_id": tool_result.metadata.get("transaction_id"), "tool": call.name, "operations": tool_result.metadata.get("operations", [])})
                if call.name == "write_file" and tool_result.metadata.get("transaction_id"):
                    write_event = "patch_commit" if tool_result.ok else "patch_refused"
                    self._record(write_event, {"transaction_id": tool_result.metadata.get("transaction_id"), "ok": tool_result.ok, "error": tool_result.metadata.get("error"), "path": tool_result.metadata.get("path"), "operation": tool_result.metadata.get("operation")})
                    if tool_result.ok:
                        self._record("transaction_committed", {"transaction_id": tool_result.metadata.get("transaction_id"), "tool": call.name, "path": tool_result.metadata.get("path"), "operation": tool_result.metadata.get("operation")})
                if call.name == "run_command":
                    command_event = "command_timeout" if tool_result.metadata.get("timed_out") else ("command_result" if tool_result.metadata.get("error") != "risk_blocked" else "command_refusal")
                    self._record(command_event, {"id": call.id, "ok": tool_result.ok, "error": tool_result.metadata.get("error"), "risk": tool_result.metadata.get("risk"), "exit_code": tool_result.metadata.get("exit_code"), "timed_out": tool_result.metadata.get("timed_out", False), "duration_seconds": tool_result.metadata.get("duration_seconds")})
                self._checkpoint(reason=f"tool:{call.name}")
                messages.append(Message(role="tool", content=self._tool_message_content(tool_result), tool_call_id=call.id))
                conflict_error = tool_result.metadata.get("error") if isinstance(tool_result.metadata, dict) else None
                if conflict_error in {"stale_context", "context_revalidation_failed", "concurrency_conflict", "transaction_prepare_failed", "transaction_commit_failed", "hook_failed_after_effect"}:
                    if not self.lifecycle.terminal:
                        try:
                            self._transition(RunState.RECOVERY_REQUIRED, reason=str(conflict_error))
                        except LifecycleError:
                            self.lifecycle.state = RunState.RECOVERY_REQUIRED
                    self._record("recovery_conflict", {"step": step, "id": call.id, "tool": call.name, "error": conflict_error, "reason": tool_result.output[:1_000]})
                    conflict_message = tool_result.output[:1_800]
                    if conflict_error and conflict_error not in conflict_message:
                        conflict_message = f"{conflict_message} [{conflict_error}]"
                    result = LoopResult(tuple(messages), "recovery_conflict", conflict_message[:2_000], verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
                    self._record("final", {"stopped_reason": result.stopped_reason, "error": result.error, "state": result.state})
                    return result
                if unresolved:
                    if not self.lifecycle.terminal:
                        try:
                            self._transition(RunState.RECOVERY_REQUIRED, reason="side-effect outcome unresolved")
                        except LifecycleError:
                            self.lifecycle.state = RunState.RECOVERY_REQUIRED
                    unresolved_message = (tool_result.output or "side-effect outcome is unresolved")[:2_000]
                    self._record("recovery_conflict", {"step": step, "id": call.id, "tool": call.name, "error": "unresolved_side_effect", "reason": unresolved_message})
                    result = LoopResult(tuple(messages), "recovery_conflict", unresolved_message, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
                    self._record("final", {"stopped_reason": result.stopped_reason, "error": result.error, "state": result.state, "pending_actions": list(self._pending_actions)})
                    return result
                if self.context.cancelled:
                    return self._cancelled_result(messages, verification_ok, explored, reason=self.context.cancellation_reason)
                if side_effecting and self.lifecycle.state is RunState.ACTING:
                    self._transition(RunState.DISCOVERING, reason="tool result recorded")

        error_text = f"maximum agent steps reached ({self.config.max_steps})"
        self._fail_state("step budget exhausted")
        result = LoopResult(tuple(messages), "max_steps", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
        self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text, "verification_ok": verification_ok})
        return result

    @staticmethod
    def _tool_message_content(tool_result) -> str:
        metadata = json.dumps(tool_result.metadata, ensure_ascii=False, sort_keys=True, default=str)
        return f"{tool_result.output}\n[metadata] {metadata}"

    def _with_current_audit(self, result: LoopResult) -> LoopResult:
        if result.audit_complete == self.audit_complete:
            return result
        return LoopResult(
            messages=result.messages,
            stopped_reason=result.stopped_reason,
            error=result.error,
            verification_ok=result.verification_ok,
            mode=result.mode,
            plan_summary=result.plan_summary,
            explored=result.explored,
            state=result.state,
            run_id=result.run_id,
            audit_complete=self.audit_complete,
            verifications=result.verifications,
        )
