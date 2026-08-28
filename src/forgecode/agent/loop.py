"""The provider-neutral model -> tool -> result loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import time
import math
from typing import Any, Callable

from ..models import Message, ModelProvider, ProviderError, is_valid_response
from ..context import RepositoryMapBuilder
from ..security.redaction import redact_text
from ..storage import Checkpoint, CheckpointStore, FileFingerprint, SessionStore, bounded
from ..tools import AgentMode, ToolContext, ToolRegistry
from .context import ContextBuilder
from .lifecycle import LifecycleError, RunLifecycle, RunState
from .verification import VerificationResult


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 12
    max_repeated_calls: int = 2
    verification_command: str | None = None
    max_verification_attempts: int = 2
    total_timeout_seconds: float = 600.0
    provider_timeout_seconds: float = 90.0
    max_tool_calls_per_turn: int = 256
    max_tool_calls_total: int = 512


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
    ):
        self.provider = provider
        self.registry = registry
        self.session = session
        self.config = config or AgentConfig()
        self.context_builder = context_builder or ContextBuilder()
        self.on_event = on_event
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
        integer_limits = (self.config.max_steps, self.config.max_repeated_calls, self.config.max_verification_attempts, self.config.max_tool_calls_per_turn, self.config.max_tool_calls_total)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in integer_limits):
            raise ValueError("loop limits must be positive")
        timeouts = (self.config.total_timeout_seconds, self.config.provider_timeout_seconds)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in timeouts):
            raise ValueError("loop timeouts must be positive")
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

        self.context = ToolContext(context.guard, context.approval, approval_observer=record_approval, mode=context.mode, secrets=context.secrets, deadline_monotonic=time.monotonic() + self.config.total_timeout_seconds, cancellation_requested=context.cancellation_requested, transaction_store=context.transaction_store, run_id=context.run_id or (session.run_id if session else ""), plan_id=context.plan_id, plan_item_id=context.plan_item_id, pre_side_effect_check=context.pre_side_effect_check, rules_fingerprint=context.rules_fingerprint, plan_fingerprint=context.plan_fingerprint, config_fingerprint=context.config_fingerprint, hooks=context.hooks)

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
        self._record("run_started", {"run_id": self.run_id, "mode": self.context.mode.value})
        self._transition(RunState.DISCOVERING, reason="task accepted")
        self._record("mode", {"mode": self.context.mode.value, "side_effects_allowed": self.context.mode is AgentMode.ACT})
        self._record("user_message", {"content": prompt, "mode": self.context.mode.value})
        self._last_context_summary = prompt[:8_000]
        seen_calls: dict[str, int] = {}
        verification_runs = 0
        verification_ok: bool | None = None
        explored: list[str] = []
        total_tool_calls = 0

        for step in range(self.config.max_steps):
            if self._pause_requested:
                if not self.lifecycle.terminal:
                    try:
                        self._transition(RunState.PAUSED, reason="cooperative pause requested")
                    except LifecycleError:
                        self.lifecycle.state = RunState.PAUSED
                result = LoopResult(tuple(messages), "paused", "run paused; resume requires checkpoint validation", verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete, verifications=tuple(self._verification_results))
                self._record("pause", {"reason": "cooperative pause requested"})
                self._record("final", {"stopped_reason": result.stopped_reason, "state": result.state})
                return result
            request_messages = self.context_builder.fit(messages)
            self._last_context_summary = "\n".join(message.content for message in request_messages[-8:])[:8_000]
            self._record("model_request", {"step": step, "message_count": len(request_messages), "context_chars": sum(len(message.content) for message in request_messages), "tool_count": len(self.registry.schemas(self.context.mode))})
            provider_started = time.monotonic()
            try:
                if self.context.hooks is not None:
                    hook_issues = self.context.hooks.emit("before_model", {"step": step, "message_count": len(request_messages), "tool_count": len(self.registry.schemas(self.context.mode))})
                    if any(issue.blocked for issue in hook_issues):
                        raise ProviderError("model request blocked by lifecycle hook", category="hook_blocked")
                remaining = self.context.remaining_seconds(self.config.provider_timeout_seconds)
                if remaining <= 0:
                    raise ProviderError("run deadline exceeded before provider request", category="deadline_exceeded")
                response = await asyncio.wait_for(self.provider.complete(request_messages, self.registry.schemas(self.context.mode)), timeout=remaining)
                if self.context.hooks is not None:
                    hook_issues = self.context.hooks.emit("after_model", {"step": step, "finish_reason": getattr(response, "finish_reason", None), "tool_calls": len(getattr(getattr(response, "message", None), "tool_calls", ()))})
                    if any(issue.blocked for issue in hook_issues):
                        raise ProviderError("model response blocked by lifecycle hook", category="hook_blocked")
                for retry in getattr(self.provider, "retry_events", ()):
                    self._record("provider_retry", retry)
            except (KeyboardInterrupt, asyncio.CancelledError):
                error_text = "agent interrupted by user"
                if not self.lifecycle.terminal:
                    try:
                        self._transition(RunState.CANCELLED, reason="user interruption")
                    except LifecycleError:
                        self.lifecycle.state = RunState.CANCELLED
                self._record("final", {"stopped_reason": "interrupted", "error": error_text})
                return LoopResult(tuple(messages), "interrupted", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
            except ProviderError as exc:
                error_text = str(exc)
                for retry in getattr(self.provider, "retry_events", ()):
                    self._record("provider_retry", retry)
                self._fail_state("provider error")
                self._record("error", {"category": exc.category, "message": error_text})
                result = LoopResult(tuple(messages), "provider_error", error_text, verification_ok, self.context.mode.value, explored=tuple(explored), state=self.lifecycle.state.value, run_id=self.run_id, audit_complete=self.audit_complete)
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result
            except asyncio.TimeoutError:
                error_text = "model request exceeded the run/provider deadline"
                self._fail_state("provider deadline")
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
                if side_effecting and self.context.mode is AgentMode.ACT:
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
                tool_started = time.monotonic()
                tool_result = self.registry.execute(call.name, call.arguments, self.context)
                tool_duration = round(time.monotonic() - tool_started, 3)
                if side_effecting:
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
