"""Application-level run assembly shared by CLI and interactive clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any, Callable

from ..agent import AgentConfig, AgentLoop, ContextBuilder, LoopResult
from ..config import EffectiveConfig
from ..models import CancellationToken, ModelProvider
from ..security.workspace import WorkspaceGuard
from ..telemetry import Telemetry
from ..storage import CheckpointStore, SessionFormatError, SessionStore, TransactionStore
from ..tools import ToolContext, ToolRegistry
from ..hooks import HookRegistry


@dataclass
class RunService:
    """Construct an AgentLoop without printing or owning CLI policy."""

    provider: ModelProvider
    registry: ToolRegistry
    guard: WorkspaceGuard
    session: SessionStore
    config: AgentConfig = AgentConfig()
    effective_config: EffectiveConfig | None = None
    approval: Any | None = None
    transaction_store: TransactionStore | None = None
    plan_id: str | None = None
    plan_item_id: str | None = None
    rules_fingerprint: str = ""
    plan_fingerprint: str = ""
    config_fingerprint: str = ""
    pre_side_effect_check: Callable[[], bool | str] | None = None
    hooks: HookRegistry | None = None
    cancellation_token: CancellationToken | None = None
    interactive_controls: bool = False
    _active_loop: AgentLoop | None = field(default=None, init=False, repr=False)
    _active_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _pending_pause: bool = field(default=False, init=False, repr=False)
    _pending_cancel: str | None = field(default=None, init=False, repr=False)
    _starting: bool = field(default=False, init=False, repr=False)

    def enable_interactive_controls(self) -> None:
        self.interactive_controls = True
        # Mark assembly as addressable before ``execute`` creates its loop so
        # a racing /pause or /cancel is retained rather than reported as an
        # idle no-op.
        with self._active_lock:
            self._starting = True

    def _current_loop(self) -> AgentLoop | None:
        with self._active_lock:
            return self._active_loop

    def pause(self) -> dict[str, Any]:
        loop = self._current_loop()
        if loop is None:
            with self._active_lock:
                if self._starting:
                    self._pending_pause = True
                    return {"paused": True, "pending": True, "message": "pause will apply when the worker is initialized"}
            return {"paused": False, "error": "no active worker"}
        if loop.lifecycle.terminal:
            return {"paused": False, "error": "worker is already terminal", "state": loop.lifecycle.state.value}
        loop.pause()
        return {"paused": True, "state": loop.lifecycle.state.value, "message": "pause requested at the next safe boundary"}

    def resume(self) -> dict[str, Any]:
        loop = self._current_loop()
        if loop is None:
            with self._active_lock:
                # A synchronous pre-loop operation (such as an interactive
                # command shortcut) uses the same service as its cancellation
                # boundary.  Allow /resume to release a pause retained during
                # that initialization window instead of reporting a false
                # idle worker.
                if self._starting and self._pending_pause:
                    self._pending_pause = False
                    return {"resumed": True, "pending": False, "message": "pending pause released before worker initialization"}
            return {"resumed": False, "error": "no active worker"}
        if loop.lifecycle.terminal:
            return {"resumed": False, "error": "worker is already terminal", "state": loop.lifecycle.state.value}
        if loop.lifecycle.state.value == "paused":
            validation_error = self._resume_validation_error(loop)
            if validation_error is not None:
                try:
                    self.session.append(
                        "resume_rejected",
                        {"reason": validation_error[:2_000], "state": loop.lifecycle.state.value},
                        mode=loop.context.mode.value,
                        outcome="rejected",
                        error_code="resume_validation_failed",
                    )
                except Exception:
                    pass
                return {"resumed": False, "error": validation_error, "code": "resume_validation_failed", "state": loop.lifecycle.state.value}
        released = loop.resume()
        return {"resumed": released, "state": loop.lifecycle.state.value, "message": "resume requested" if released else "worker was not paused"}

    def _resume_validation_error(self, loop: AgentLoop) -> str | None:
        """Revalidate durable session/checkpoint and planning fingerprints."""
        try:
            read_result = self.session.read_with_issues(strict=True)
            if any(event.run_id != self.session.run_id for event in read_result.events if event.schema_version >= 1):
                return "session run_id changed while paused"
        except (OSError, SessionFormatError, ValueError) as exc:
            return f"session validation failed: {type(exc).__name__}"
        try:
            checkpoint = CheckpointStore(self.session.path.with_suffix(".checkpoint.json")).load()
        except FileNotFoundError:
            return "paused run has no checkpoint"
        except (OSError, ValueError) as exc:
            return f"checkpoint validation failed: {type(exc).__name__}"
        if checkpoint.state != "paused":
            return "checkpoint is not in paused state"
        conflicts = CheckpointStore(self.session.path.with_suffix(".checkpoint.json")).validate(
            checkpoint,
            self.guard,
            expected_run_id=self.session.run_id,
            rules_fingerprint=loop.context.rules_fingerprint or None,
            plan_fingerprint=loop.context.plan_fingerprint or None,
            config_fingerprint=loop.context.config_fingerprint or None,
        )
        if conflicts:
            return "resume checkpoint conflict: " + "; ".join(item.reason for item in conflicts[:8])
        if self.pre_side_effect_check is not None:
            try:
                checked = self.pre_side_effect_check()
            except Exception as exc:
                return f"resume context validation failed: {type(exc).__name__}"
            if checked is not True:
                return str(checked or "rules/config/context changed while paused")[:2_000]
        return None

    def cancel(self, reason: str = "interactive cancel") -> dict[str, Any]:
        safe_reason = str(reason or "interactive cancel")[:256]
        # A command shortcut can be executing before AgentLoop has been
        # constructed.  Cancel the shared token immediately so ShellTool can
        # terminate its process instead of waiting for a later loop boundary.
        if self.cancellation_token is not None:
            self.cancellation_token.cancel(safe_reason)
        loop = self._current_loop()
        if loop is None:
            with self._active_lock:
                if self._starting:
                    self._pending_cancel = safe_reason
                    return {"cancelled": True, "pending": True, "message": "cancel will apply when the worker is initialized"}
            return {"cancelled": False, "error": "no active worker"}
        if loop.lifecycle.terminal:
            return {"cancelled": False, "error": "worker is already terminal", "state": loop.lifecycle.state.value}
        return {"cancelled": loop.cancel(safe_reason), "message": safe_reason}

    async def execute(
        self,
        prompt: str,
        *,
        mode: str = "act",
        secrets: tuple[str, ...] = (),
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> LoopResult:
        with self._active_lock:
            self._starting = True
        transaction_store = self.transaction_store or TransactionStore(self.guard, max_total_bytes=self.effective_config.transaction_max_bytes if self.effective_config else 50_000_000)
        token = cancellation_token or self.cancellation_token
        context = ToolContext(self.guard, self.approval, mode=mode, secrets=secrets, cancellation_token=token, transaction_store=transaction_store, run_id=self.session.run_id, plan_id=self.plan_id, plan_item_id=self.plan_item_id, rules_fingerprint=self.rules_fingerprint, plan_fingerprint=self.plan_fingerprint, config_fingerprint=self.config_fingerprint, pre_side_effect_check=self.pre_side_effect_check, hooks=self.hooks)
        context_builder = ContextBuilder(max_chars=self.effective_config.context_budget_chars if self.effective_config else 60_000)
        loop = AgentLoop(self.provider, self.registry, context, session=self.session, config=self.config, context_builder=context_builder, on_event=on_event, cancellation_token=token)
        if self.interactive_controls:
            loop.enable_interactive_controls()
        with self._active_lock:
            self._active_loop = loop
            self._starting = False
            pending_pause = self._pending_pause
            pending_cancel = self._pending_cancel
            self._pending_pause = False
            self._pending_cancel = None
        if pending_cancel is not None:
            loop.cancel(pending_cancel)
        elif pending_pause:
            loop.pause()
        try:
            result = await loop.run(prompt)
            if self.effective_config is not None:
                # Record only bounded outcome metadata; prompt, tool payloads,
                # credentials, and workspace paths never enter telemetry.
                try:
                    Telemetry(self.guard.root, mode=self.effective_config.telemetry, offline=self.effective_config.offline).record(
                        "run_finished", stopped_reason=result.stopped_reason, state=result.state,
                        succeeded=bool(result.succeeded), verification_ok=result.verification_ok,
                    )
                except (OSError, ValueError):
                    pass
            return result
        finally:
            with self._active_lock:
                if self._active_loop is loop:
                    self._active_loop = None
                self._starting = False


__all__ = ["RunService"]
