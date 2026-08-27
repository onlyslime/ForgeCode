"""Typed run lifecycle used by the agent loop and durable sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class RunState(StrEnum):
    CREATED = "created"
    DISCOVERING = "discovering"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTING = "acting"
    VERIFYING = "verifying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERY_REQUIRED = "recovery_required"


class LifecycleError(ValueError):
    """Raised when a run attempts an unsafe or impossible state transition."""


@dataclass
class RunLifecycle:
    """Small checked state machine; no transition is implicit."""

    state: RunState = RunState.CREATED

    _transitions: ClassVar[dict[RunState, frozenset[RunState]]] = {
        RunState.CREATED: frozenset({RunState.DISCOVERING, RunState.CANCELLED, RunState.FAILED}),
        RunState.DISCOVERING: frozenset({RunState.PLANNING, RunState.FAILED, RunState.CANCELLED, RunState.RECOVERY_REQUIRED}),
        RunState.PLANNING: frozenset({RunState.AWAITING_APPROVAL, RunState.VERIFYING, RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.PAUSED}),
        RunState.AWAITING_APPROVAL: frozenset({RunState.ACTING, RunState.PAUSED, RunState.CANCELLED, RunState.FAILED, RunState.RECOVERY_REQUIRED}),
        RunState.ACTING: frozenset({RunState.DISCOVERING, RunState.VERIFYING, RunState.COMPLETED, RunState.PAUSED, RunState.FAILED, RunState.CANCELLED, RunState.RECOVERY_REQUIRED}),
        RunState.VERIFYING: frozenset({RunState.COMPLETED, RunState.ACTING, RunState.AWAITING_APPROVAL, RunState.DISCOVERING, RunState.PAUSED, RunState.FAILED, RunState.CANCELLED, RunState.RECOVERY_REQUIRED}),
        RunState.PAUSED: frozenset({RunState.DISCOVERING, RunState.CANCELLED, RunState.FAILED, RunState.RECOVERY_REQUIRED}),
        RunState.RECOVERY_REQUIRED: frozenset({RunState.DISCOVERING, RunState.CANCELLED, RunState.FAILED}),
        RunState.COMPLETED: frozenset(),
        RunState.FAILED: frozenset(),
        RunState.CANCELLED: frozenset(),
    }

    @property
    def terminal(self) -> bool:
        return self.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}

    def can_transition(self, target: RunState | str) -> bool:
        try:
            target_state = RunState(target)
        except (TypeError, ValueError):
            return False
        return target_state in self._transitions[self.state]

    def transition(self, target: RunState | str) -> tuple[RunState, RunState]:
        try:
            target_state = RunState(target)
        except (TypeError, ValueError) as exc:
            raise LifecycleError(f"unknown run state: {target!r}") from exc
        if not self.can_transition(target_state):
            raise LifecycleError(f"invalid run state transition: {self.state.value} -> {target_state.value}")
        previous = self.state
        self.state = target_state
        return previous, target_state

    @classmethod
    def from_state(cls, state: RunState | str) -> "RunLifecycle":
        return cls(RunState(state))


__all__ = ["LifecycleError", "RunLifecycle", "RunState"]
