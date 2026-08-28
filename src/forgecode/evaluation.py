"""Durable trajectory evaluation for deterministic and live runs.

The evaluator intentionally consumes only persisted session events.  Model
prose is retained as evidence but never overrides exit codes, verification,
approval, cancellation, or audit state.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from typing import Any, Iterable

from .storage import SessionEvent


@dataclass(frozen=True)
class TrajectoryScore:
    status: str
    score: float
    task_completed: bool
    verification_passed: bool
    audit_complete: bool
    tool_calls: int
    side_effects: int
    failures: int
    repair_rounds: int
    repeated_tool_calls: int
    approvals_denied: int
    compactions: int
    conflicts: int
    cancelled: bool
    unresolved: bool
    duration_seconds: float | None
    evidence_sequences: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_sequences"] = list(self.evidence_sequences)
        return payload


def evaluate_events(events: Iterable[SessionEvent]) -> TrajectoryScore:
    records = tuple(events)
    tool_calls = sum(event.kind == "tool_call" for event in records)
    side_effects = sum(event.kind in {"patch_commit", "transaction_committed", "command_result", "command_timeout"} for event in records)

    def _failed(event: SessionEvent) -> bool:
        if event.kind in {"error", "command_timeout", "command_refusal"}:
            return True
        if event.kind == "command_result":
            exit_code = event.payload.get("exit_code")
            if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                return exit_code != 0
        return event.kind in {"tool_result", "verification_result", "test_profile_result", "test_result", "test_finished"} and event.payload.get("ok") is False

    failures = sum(_failed(event) for event in records)
    repair_rounds = sum(event.kind == "verification_result" and event.payload.get("ok") is False for event in records)
    denied = sum(event.kind == "approval" and not bool(event.payload.get("approved")) for event in records)
    conflicts = sum(event.kind in {"recovery_conflict", "transaction_conflict"} for event in records)
    unresolved = any(bool(event.payload.get("unresolved")) or event.outcome == "unresolved" for event in records)
    cancelled = any(event.kind == "cancelled" or event.error_code == "cancelled" or event.payload.get("category") == "cancelled" for event in records)
    verification = [event for event in records if event.kind in {"verification_result", "transaction_verification", "test_profile_result", "test_result", "test_finished"}]
    def _verification_ok(event: SessionEvent) -> bool:
        nested = event.payload.get("verification")
        return bool(event.payload.get("ok")) or (isinstance(nested, dict) and bool(nested.get("ok")))
    verification_events = [event for event in records if event.kind == "verification_result"] or verification
    verification_passed = bool(verification_events) and _verification_ok(verification_events[-1])
    final = next((event for event in reversed(records) if event.kind == "final"), None)
    final_reason = final.payload.get("stopped_reason") if final else None
    terminal_state = next((event.payload.get("to") for event in reversed(records) if event.kind == "state_transition" and isinstance(event.payload.get("to"), str)), None)
    if terminal_state is None:
        terminal_state = next((event.payload.get("state") for event in reversed(records) if event.kind == "checkpoint" and isinstance(event.payload.get("state"), str)), None)
    if terminal_state is None and final and isinstance(final.payload.get("state"), str):
        terminal_state = final.payload["state"]
    task_completed = bool(final_reason == "model_finished" and terminal_state == "completed")
    audit_complete = not any(event.kind == "session_error" for event in records)
    status = "completed" if task_completed and verification_passed and audit_complete and not conflicts and not unresolved else ("cancelled" if cancelled else ("recovery_required" if conflicts or unresolved else "failed"))
    score = 0.0
    score += 0.4 if task_completed else 0.0
    score += 0.3 if verification_passed else 0.0
    score += 0.2 if audit_complete else 0.0
    score += 0.1 if not conflicts and not unresolved else 0.0
    start = records[0].timestamp if records else None
    end = records[-1].timestamp if records else None
    duration = None
    if start and end:
        try:
            from datetime import datetime
            duration = max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
        except (TypeError, ValueError):
            duration = None
    return TrajectoryScore(status, round(score, 3), task_completed, verification_passed, audit_complete, tool_calls, side_effects, failures, repair_rounds, max(0, tool_calls - len({event.payload.get("id") for event in records if event.kind == "tool_call"})), denied, sum(event.kind == "context_compacted" for event in records), conflicts, cancelled, unresolved, duration, tuple(event.sequence for event in records[-32:] if event.sequence))


def evaluate_session(store) -> TrajectoryScore:
    result = store.read_with_issues()
    score = evaluate_events(result.events)
    if result.issues:
        return replace(score, status="recovery_required", audit_complete=False, score=0.0)
    return score


__all__ = ["TrajectoryScore", "evaluate_events", "evaluate_session"]
