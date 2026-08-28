"""Read-only session application queries and deterministic reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent import ContextCompactor, RebuiltContext, SessionContextRebuilder
from ..security.workspace import WorkspaceGuard
from ..storage import CheckpointStore, SessionStore
from ..evaluation import evaluate_events


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

    def tree(self, *, limit: int = 200) -> dict[str, Any]:
        """Build a bounded parent/child view from durable fork metadata."""
        directory = self.guard.resolve(Path(".forgecode") / "sessions")
        nodes: dict[str, dict[str, Any]] = {}
        if not directory.is_dir():
            return {"nodes": [], "roots": [], "edges": []}
        for path in sorted(directory.glob("*.jsonl"), key=lambda item: item.name)[:limit]:
            try:
                resolved = self.guard.resolve(path, must_exist=True)
                store = SessionStore(resolved)
                result = store.read_with_issues()
                events = result.events
                run_id = events[0].run_id if events else store.run_id
                # ``clone`` and ``import`` are also branches in the evidence
                # tree.  They carry the same parent identity (or an imported
                # source identity) but never replay the source side effects.
                branch = next((event.payload for event in events if event.kind in {"forked", "cloned", "imported"}), {})
                parent_run_id = branch.get("parent_run_id") or branch.get("source_run_id")
                parent_sequence = branch.get("parent_sequence")
                if parent_sequence is None:
                    parent_sequence = branch.get("source_sequence_end")
                state = next((event.payload.get("to") for event in reversed(events) if event.kind == "state_transition"), None)
                if not state:
                    state = next((event.payload.get("state") for event in reversed(events) if event.kind in {"checkpoint", "final"} and isinstance(event.payload.get("state"), str)), "unknown")
                mode = next((event.mode for event in reversed(events) if event.mode), None)
                profile = next((event.payload.get("to") for event in reversed(events) if event.kind == "profile_switch" and isinstance(event.payload.get("to"), str)), None)
                if profile is None:
                    profile = next((event.payload.get("profile") for event in reversed(events) if isinstance(event.payload.get("profile"), str)), None)
                nodes[run_id] = {"run_id": run_id, "session": self.guard.relative(resolved), "parent_run_id": parent_run_id, "parent_sequence": parent_sequence, "sequence": max((event.sequence for event in events), default=0), "state": state, "mode": mode, "profile": profile, "events": len(events), "issues": len(result.issues), "inspect_only": state == "completed" or any(event.kind == "imported" for event in events)}
            except (OSError, ValueError):
                continue
        edges = [{"parent": item["parent_run_id"], "child": item["run_id"], "sequence": item.get("parent_sequence")} for item in nodes.values() if item.get("parent_run_id") in nodes]
        roots = sorted(run_id for run_id, item in nodes.items() if not item.get("parent_run_id") or item.get("parent_run_id") not in nodes)
        return {"nodes": sorted(nodes.values(), key=lambda item: item["run_id"])[:limit], "roots": roots[:limit], "edges": edges[:limit]}


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
    trajectory = evaluate_events(events).to_dict()
    return {"start": start, "end": end, "provider_attempts": sum(1 for event in events if event.kind == "model_request"), "provider_retries": sum(1 for event in events if event.kind == "provider_retry"), "context_chars": context_chars, "compactions": sum(1 for event in events if event.kind == "context_compacted"), "tool_counts": dict(sorted(tool_counts.items())), "approvals": approvals, "transactions": sum(1 for event in events if event.kind == "transaction_committed"), "latest_verification": latest_verification, "latest_error": latest_error, "audit_complete": not any(event.kind == "session_error" for event in events), "trajectory": trajectory}


__all__.append("aggregate_events")
