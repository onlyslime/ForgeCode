"""Read-only session application queries and deterministic reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent import ContextCompactor, RebuiltContext, SessionContextRebuilder
from ..security.workspace import WorkspaceGuard
from ..storage import CheckpointStore, SessionStore


@dataclass(frozen=True)
class SessionService:
    guard: WorkspaceGuard

    def store(self, path: Path) -> SessionStore:
        return SessionStore(self.guard.resolve(path))

    def inspect(self, path: Path) -> dict[str, Any]:
        store = self.store(path)
        result = store.read_with_issues()
        rebuilt = SessionContextRebuilder().rebuild(store)
        return {"id": path.stem, "events": len(result.events), "issues": [issue.__dict__ for issue in result.issues], "context": rebuilt.to_dict(), "metrics": aggregate_events(result.events)}

    def compact(self, path: Path, *, max_chars: int = 24_000) -> dict[str, Any]:
        store = self.store(path)
        result = ContextCompactor(max_chars=max_chars).compact_store(store)
        return result.to_dict()


__all__ = ["SessionService"]


def aggregate_events(events) -> dict[str, Any]:
    """Produce bounded evidence metrics from the append-only event stream."""
    tool_counts: dict[str, int] = {}
    approvals = {"approved": 0, "denied": 0}
    context_chars = 0
    latest_error = None
    latest_verification = None
    start = None
    end = None
    for event in events:
        start = start or event.timestamp
        end = event.timestamp
        if event.kind == "tool_call":
            name = str(event.payload.get("tool", "unknown"))
            tool_counts[name] = tool_counts.get(name, 0) + 1
        elif event.kind == "approval":
            approvals["approved" if event.payload.get("approved") else "denied"] += 1
        elif event.kind == "model_request":
            value = event.payload.get("context_chars", 0)
            if isinstance(value, int) and not isinstance(value, bool): context_chars += value
        elif event.kind == "error": latest_error = event.payload
        elif event.kind == "verification_result": latest_verification = event.payload
    return {"start": start, "end": end, "provider_attempts": sum(1 for event in events if event.kind == "model_request"), "provider_retries": sum(1 for event in events if event.kind == "provider_retry"), "context_chars": context_chars, "compactions": sum(1 for event in events if event.kind == "context_compacted"), "tool_counts": dict(sorted(tool_counts.items())), "approvals": approvals, "transactions": sum(1 for event in events if event.kind == "transaction_committed"), "latest_verification": latest_verification, "latest_error": latest_error, "audit_complete": not any(event.kind == "session_error" for event in events)}


__all__.append("aggregate_events")
