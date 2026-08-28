"""Context reconstruction and deterministic compaction for durable sessions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Sequence

from ..models import Message
from ..storage import Checkpoint, SessionEvent, SessionFormatError, SessionStore


@dataclass(frozen=True)
class CompactionResult:
    before_chars: int
    after_chars: int
    omitted_messages: int
    omitted_events: int
    source_sequence_start: int | None
    source_sequence_end: int | None
    retained_sections: tuple[str, ...]
    summary: str
    # ``reason`` and ``context_fingerprint`` are additive fields used by the
    # automatic rolling-window path.  Defaults preserve the v0.0.8 API for
    # callers that construct/compare results positionally.
    reason: str = "manual"
    context_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_chars": self.before_chars,
            "after_chars": self.after_chars,
            "omitted_messages": self.omitted_messages,
            "omitted_events": self.omitted_events,
            "source_sequence_start": self.source_sequence_start,
            "source_sequence_end": self.source_sequence_end,
            "retained_sections": list(self.retained_sections),
            "summary": self.summary,
            "reason": self.reason,
            "context_fingerprint": self.context_fingerprint,
        }


class ContextCompactor:
    """Summarise facts from events without rewriting the append-only log."""

    PRIORITY = ("system_safety", "rules", "user_intent", "plan", "checkpoint", "transactions", "verification", "references", "recent_messages", "older_history")

    def __init__(self, *, max_chars: int = 24_000, recent_events: int = 24):
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 256:
            raise ValueError("compaction max_chars must be >= 256")
        if isinstance(recent_events, bool) or not isinstance(recent_events, int) or recent_events < 1:
            raise ValueError("recent_events must be positive")
        self.max_chars = max_chars
        self.recent_events = recent_events

    def compact_events(self, events: Sequence[SessionEvent], *, current_messages: Sequence[Message] = (), rules: str = "", plan: dict[str, Any] | None = None, checkpoint: Checkpoint | None = None, references: str = "", reason: str = "manual") -> CompactionResult:
        before = sum(len(json.dumps(event.payload, ensure_ascii=False, default=str)) for event in events) + sum(len(message.content) for message in current_messages)
        sections: list[tuple[str, str]] = []
        sections.append(("system_safety", "Safety boundary: WorkspaceGuard, approval, mode restrictions, hard blocks and hash conflicts remain authoritative."))
        if rules:
            sections.append(("rules", "Active rules fingerprinted context:\n" + rules[:6_000]))
        user_events = [event for event in events if event.kind == "user_message"]
        if user_events:
            sections.append(("user_intent", "User intent:\n" + str(user_events[-1].payload.get("content", ""))[:6_000]))
        if plan:
            sections.append(("plan", "Structured plan evidence:\n" + json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str)[:8_000]))
        if checkpoint:
            sections.append(("checkpoint", f"Checkpoint state={checkpoint.state} sequence={checkpoint.sequence} pending={len(checkpoint.pending_actions)} files={len(checkpoint.files)}"))
        transaction_events = [event for event in events if event.kind in {"patch_commit", "patch_rollback", "transaction_committed", "transaction_undo", "transaction_conflict"}]
        if transaction_events:
            sections.append(("transactions", "Transaction evidence:\n" + "\n".join(self._event_line(event) for event in transaction_events[-16:])))
        verification_events = [event for event in events if "verification" in event.kind or event.kind in {"command_result", "command_timeout"}]
        if verification_events:
            sections.append(("verification", "Verification evidence:\n" + "\n".join(self._event_line(event) for event in verification_events[-12:])))
        if references:
            sections.append(("references", "Explicit references:\n" + references[:6_000]))
        recent = list(events[-self.recent_events:])
        if recent:
            sections.append(("recent_messages", "Recent event facts:\n" + "\n".join(self._event_line(event) for event in recent)))
        rendered: list[str] = []
        retained: list[str] = []
        used = 0
        for name, content in sections:
            chunk = f"[{name}]\n{content}"
            if used + len(chunk) > self.max_chars:
                continue
            rendered.append(chunk); retained.append(name); used += len(chunk) + 2
        summary = "\n\n".join(rendered)
        sequences = [event.sequence for event in events if event.sequence]
        fingerprint = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        return CompactionResult(before, len(summary), max(0, len(current_messages) - self.recent_events), max(0, len(events) - self.recent_events), min(sequences) if sequences else None, max(sequences) if sequences else None, tuple(retained), summary, str(reason or "manual")[:64], fingerprint)

    def compact_store(self, store: SessionStore, *, current_messages: Sequence[Message] = (), rules: str = "", plan: dict[str, Any] | None = None, checkpoint: Checkpoint | None = None, references: str = "", reason: str = "manual") -> CompactionResult:
        read_result = store.read_with_issues()
        if read_result.issues:
            issue = read_result.issues[0]
            raise SessionFormatError(f"cannot compact an inconsistent session (line {issue.line}: {issue.message})")
        result = self.compact_events(read_result.events, current_messages=current_messages, rules=rules, plan=plan, checkpoint=checkpoint, references=references, reason=reason)
        store.append("context_compacted", result.to_dict(), mode=store.mode)
        return result

    @staticmethod
    def _event_line(event: SessionEvent) -> str:
        payload = json.dumps(event.payload, ensure_ascii=False, sort_keys=True, default=str)
        return f"seq={event.sequence} kind={event.kind} {payload[:600]}"


@dataclass(frozen=True)
class RebuiltContext:
    messages: tuple[Message, ...]
    state: str
    run_id: str
    sequence: int
    pending_actions: tuple[dict[str, Any], ...]
    transaction_evidence: tuple[dict[str, Any], ...]
    verification_evidence: tuple[dict[str, Any], ...]
    conflicts: tuple[str, ...] = ()
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "run_id": self.run_id, "sequence": self.sequence, "pending_actions": list(self.pending_actions), "transaction_evidence": list(self.transaction_evidence), "verification_evidence": list(self.verification_evidence), "conflicts": list(self.conflicts), "fingerprint": self.fingerprint, "message_count": len(self.messages)}


class SessionContextRebuilder:
    """Rebuild provider-neutral evidence; never replays a side effect."""

    def rebuild(self, store: SessionStore, checkpoint: Checkpoint | None = None, *, max_messages: int = 64) -> RebuiltContext:
        result = store.read_with_issues()
        events = result.events
        conflicts = [f"session:{issue.line}:{issue.message}" for issue in result.issues]
        if not events:
            return RebuiltContext((), checkpoint.state if checkpoint else "unknown", store.run_id, 0, tuple(checkpoint.pending_actions) if checkpoint else (), (), (), tuple(conflicts))
        if any(event.schema_version == 0 for event in events):
            conflicts.append("legacy session schema is inspect-only")
        run_id = events[0].run_id or store.run_id
        if any(event.run_id and event.run_id != run_id for event in events):
            conflicts.append("session contains multiple run ids")
        state = "unknown"
        pending: tuple[dict[str, Any], ...] = tuple(checkpoint.pending_actions) if checkpoint else ()
        messages: list[Message] = []
        transactions: list[dict[str, Any]] = []
        verifications: list[dict[str, Any]] = []
        for event in events:
            payload = event.payload
            if event.kind == "state_transition" and isinstance(payload.get("to"), str): state = payload["to"]
            elif isinstance(payload.get("state"), str) and event.kind in {"checkpoint", "recovery_conflict"}: state = payload["state"]
            if event.kind == "user_message": messages.append(Message("user", str(payload.get("content", ""))[:16_000]))
            elif event.kind == "model_message": messages.append(Message("assistant", str(payload.get("content", ""))[:16_000]))
            elif event.kind == "tool_result": messages.append(Message("tool", str(payload.get("output", ""))[:16_000], tool_call_id=str(payload.get("id", "")) or None))
            if event.kind in {"patch_commit", "patch_rollback", "transaction_committed", "transaction_undo", "transaction_conflict"}:
                transactions.append({"sequence": event.sequence, **payload})
            if "verification" in event.kind or event.kind in {"command_result", "command_timeout"}:
                verifications.append({"sequence": event.sequence, **payload})
        if checkpoint and checkpoint.run_id != run_id:
            conflicts.append("checkpoint run_id does not match session")
        messages = messages[-max_messages:]
        sequence = max((event.sequence for event in events), default=0)
        if checkpoint:
            sequences = {event.sequence for event in events if event.sequence}
            if checkpoint.sequence < 0 or checkpoint.sequence > sequence:
                conflicts.append("checkpoint sequence is outside the session range")
            elif checkpoint.sequence and checkpoint.sequence not in sequences:
                conflicts.append("checkpoint sequence is not present in the session")
        fingerprint = hashlib.sha256(json.dumps({"run_id": run_id, "sequence": sequence, "state": state, "pending": pending, "transactions": transactions[-16:], "verifications": verifications[-16:]}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return RebuiltContext(tuple(messages), state, run_id, sequence, pending, tuple(transactions[-32:]), tuple(verifications[-32:]), tuple(conflicts), fingerprint)


__all__ = ["CompactionResult", "ContextCompactor", "RebuiltContext", "SessionContextRebuilder"]
