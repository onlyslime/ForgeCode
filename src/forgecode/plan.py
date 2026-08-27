"""Versioned structured task plans with DAG and evidence validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping
import uuid


PLAN_SCHEMA_VERSION = 1
PLAN_STATUSES = frozenset({"pending", "in_progress", "completed", "failed", "skipped", "blocked"})
_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"pending", "in_progress", "skipped", "blocked"}),
    "in_progress": frozenset({"in_progress", "completed", "failed", "blocked", "skipped"}),
    "failed": frozenset({"failed", "in_progress", "blocked", "skipped"}),
    "blocked": frozenset({"blocked", "in_progress", "skipped"}),
    "completed": frozenset({"completed"}),
    "skipped": frozenset({"skipped"}),
}


class PlanError(ValueError):
    """Plan payload or transition cannot be trusted."""


@dataclass(frozen=True)
class PlanItem:
    id: str
    title: str
    description: str = ""
    dependencies: tuple[str, ...] = ()
    risk: str = "normal"
    expected_files: tuple[str, ...] = ()
    expected_commands: tuple[str, ...] = ()
    status: str = "pending"
    acceptance_criteria: tuple[str, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()

    def validate(self) -> None:
        for field_name, value, limit, required in (("id", self.id, 80, True), ("title", self.title, 240, True), ("description", self.description, 8_000, False)):
            if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
                qualifier = "non-empty " if required else ""
                raise PlanError(f"plan item {field_name} must be a {qualifier}string <= {limit} chars")
        if self.status not in PLAN_STATUSES:
            raise PlanError(f"invalid plan item status: {self.status}")
        for field_name, values, limit in (("dependencies", self.dependencies, 32), ("expected_files", self.expected_files, 256), ("expected_commands", self.expected_commands, 64), ("acceptance_criteria", self.acceptance_criteria, 128)):
            if not isinstance(values, tuple) or len(values) > limit:
                raise PlanError(f"plan item {field_name} must be a bounded tuple")
        for name in (*self.dependencies, *self.expected_files, *self.expected_commands, *self.acceptance_criteria):
            if not isinstance(name, str) or len(name) > 2_000:
                raise PlanError("plan item field contains an invalid or oversized string")
        if self.risk not in {"low", "normal", "high", "dangerous"}:
            raise PlanError(f"invalid plan item risk: {self.risk}")
        if not isinstance(self.evidence, tuple) or len(self.evidence) > 64:
            raise PlanError("plan item evidence is too large")
        try:
            encoded_evidence = json.dumps(self.evidence, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"), default=_reject_non_json)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PlanError(f"plan item evidence is invalid: {type(exc).__name__}") from exc
        if len(encoded_evidence) > 40_000:
            raise PlanError("plan item evidence is too large")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("dependencies", "expected_files", "expected_commands", "acceptance_criteria", "evidence"):
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class TaskPlan:
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    revision: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mode: str = "plan"
    task: str = ""
    items: tuple[PlanItem, ...] = ()
    rules_fingerprint: str = ""
    context_fingerprint: str = ""
    checkpoint_fingerprint: str = ""
    stale: bool = False
    approved: bool = False
    approval_reason: str | None = None
    schema_version: int = PLAN_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != PLAN_SCHEMA_VERSION:
            raise PlanError(f"unsupported plan schema_version: {self.schema_version}")
        if not isinstance(self.plan_id, str) or not self.plan_id or len(self.plan_id) > 128 or not all(character.isalnum() or character in "-_" for character in self.plan_id):
            raise PlanError("plan_id must be a bounded non-empty string")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise PlanError("plan revision must be a positive integer")
        if self.mode not in {"plan", "act"}:
            raise PlanError("plan mode must be plan or act")
        if not isinstance(self.created_at, str) or not self.created_at or len(self.created_at) > 128:
            raise PlanError("plan created_at must be bounded text")
        if not isinstance(self.task, str) or not self.task.strip() or len(self.task) > 8_000:
            raise PlanError("plan task must be a non-empty string <= 8000 chars")
        if not isinstance(self.items, tuple) or len(self.items) > 128:
            raise PlanError("plan contains too many items")
        if not isinstance(self.stale, bool) or not isinstance(self.approved, bool):
            raise PlanError("plan stale and approved flags must be booleans")
        if self.approved and self.mode != "act":
            raise PlanError("an approved plan must be in act mode")
        if self.stale and self.approved:
            raise PlanError("a stale plan cannot remain approved")
        if self.approval_reason is not None and (not isinstance(self.approval_reason, str) or len(self.approval_reason) > 500):
            raise PlanError("plan approval_reason is invalid")
        for name, value in (("rules_fingerprint", self.rules_fingerprint), ("context_fingerprint", self.context_fingerprint), ("checkpoint_fingerprint", self.checkpoint_fingerprint)):
            if not isinstance(value, str) or len(value) > 128:
                raise PlanError(f"{name} must be bounded text")
        ids: set[str] = set()
        for item in self.items:
            if not isinstance(item, PlanItem):
                raise PlanError("plan items must be PlanItem objects")
            item.validate()
            if item.id in ids:
                raise PlanError(f"duplicate plan item id: {item.id}")
            ids.add(item.id)
        for item in self.items:
            unknown = set(item.dependencies) - ids
            if unknown:
                raise PlanError(f"plan item {item.id} has unknown dependencies: {sorted(unknown)}")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        by_id = {item.id: item for item in self.items}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item_id: str) -> None:
            if item_id in visiting:
                raise PlanError("plan dependency graph contains a cycle")
            if item_id in visited:
                return
            visiting.add(item_id)
            for dependency in by_id[item_id].dependencies:
                visit(dependency)
            visiting.remove(item_id)
            visited.add(item_id)

        for item_id in by_id:
            visit(item_id)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version, "plan_id": self.plan_id, "revision": self.revision,
            "created_at": self.created_at, "mode": self.mode, "task": self.task,
            "items": [item.to_dict() for item in self.items], "rules_fingerprint": self.rules_fingerprint,
            "context_fingerprint": self.context_fingerprint, "checkpoint_fingerprint": self.checkpoint_fingerprint,
            "stale": self.stale, "approved": self.approved, "approval_reason": self.approval_reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskPlan":
        if not isinstance(payload, Mapping):
            raise PlanError("plan must be an object")
        allowed_plan = {"schema_version", "plan_id", "revision", "created_at", "mode", "task", "items", "rules_fingerprint", "context_fingerprint", "checkpoint_fingerprint", "stale", "approved", "approval_reason"}
        unknown_plan = set(payload) - allowed_plan
        if unknown_plan:
            raise PlanError("unknown plan fields: " + ", ".join(sorted(str(key) for key in unknown_plan)))
        try:
            raw_items = payload.get("items", ())
            if not isinstance(raw_items, (list, tuple)):
                raise PlanError("plan items must be an array")
            items_list: list[PlanItem] = []
            allowed_item = {"id", "title", "description", "dependencies", "risk", "expected_files", "expected_commands", "status", "acceptance_criteria", "evidence"}
            for item in raw_items:
                if not isinstance(item, Mapping):
                    raise PlanError("plan item must be an object")
                unknown_item = set(item) - allowed_item
                if unknown_item:
                    raise PlanError("unknown plan item fields: " + ", ".join(sorted(str(key) for key in unknown_item)))
                def tuple_field(name: str) -> tuple[Any, ...]:
                    raw_value = item.get(name, ())
                    if not isinstance(raw_value, (list, tuple)):
                        raise PlanError(f"plan item {name} must be an array")
                    return tuple(raw_value)
                items_list.append(PlanItem(
                    id=item["id"], title=item["title"], description=item.get("description", ""),
                    dependencies=tuple_field("dependencies"), risk=item.get("risk", "normal"),
                    expected_files=tuple_field("expected_files"), expected_commands=tuple_field("expected_commands"),
                    status=item.get("status", "pending"), acceptance_criteria=tuple_field("acceptance_criteria"), evidence=tuple_field("evidence"),
                ))
            items = tuple(items_list)
            plan = cls(plan_id=payload.get("plan_id", uuid.uuid4().hex), revision=payload.get("revision", 1), created_at=payload.get("created_at", datetime.now(timezone.utc).isoformat()), mode=payload.get("mode", "plan"), task=payload.get("task", ""), items=items, rules_fingerprint=payload.get("rules_fingerprint", ""), context_fingerprint=payload.get("context_fingerprint", ""), checkpoint_fingerprint=payload.get("checkpoint_fingerprint", ""), stale=payload.get("stale", False), approved=payload.get("approved", False), approval_reason=payload.get("approval_reason"), schema_version=payload.get("schema_version", PLAN_SCHEMA_VERSION))
        except PlanError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError(f"invalid plan payload: {type(exc).__name__}") from exc
        plan.validate()
        return plan

    def revise(self, *, items: Iterable[PlanItem] | None = None, task: str | None = None, mode: str | None = None, rules_fingerprint: str | None = None, context_fingerprint: str | None = None, checkpoint_fingerprint: str | None = None) -> "TaskPlan":
        next_plan = replace(self, revision=self.revision + 1, items=tuple(items) if items is not None else self.items, task=task if task is not None else self.task, mode=mode if mode is not None else self.mode, rules_fingerprint=rules_fingerprint if rules_fingerprint is not None else self.rules_fingerprint, context_fingerprint=context_fingerprint if context_fingerprint is not None else self.context_fingerprint, checkpoint_fingerprint=checkpoint_fingerprint if checkpoint_fingerprint is not None else self.checkpoint_fingerprint, stale=False, approved=False, approval_reason=None)
        next_plan.validate()
        return next_plan

    def mark_stale_if_changed(self, *, rules_fingerprint: str | None = None, context_fingerprint: str | None = None, checkpoint_fingerprint: str | None = None) -> "TaskPlan":
        changed = ((rules_fingerprint is not None and rules_fingerprint != self.rules_fingerprint) or (context_fingerprint is not None and context_fingerprint != self.context_fingerprint) or (checkpoint_fingerprint is not None and checkpoint_fingerprint != self.checkpoint_fingerprint))
        return replace(self, stale=True, approved=False, approval_reason=None) if changed else self

    def update_status(self, item_id: str, status: str, *, evidence: Mapping[str, Any] | None = None) -> "TaskPlan":
        if status not in PLAN_STATUSES:
            raise PlanError(f"invalid status: {status}")
        found = False
        updated: list[PlanItem] = []
        for item in self.items:
            if item.id != item_id:
                updated.append(item); continue
            found = True
            if status not in _ALLOWED_TRANSITIONS[item.status]:
                raise PlanError(f"invalid status transition {item.status} -> {status} for {item_id}")
            if status in {"in_progress", "completed"}:
                by_id = {candidate.id: candidate for candidate in self.items}
                blocked = [dependency for dependency in item.dependencies if by_id[dependency].status not in {"completed", "skipped"}]
                if blocked:
                    raise PlanError(f"plan item {item_id} dependencies are not complete: {', '.join(blocked)}")
            if evidence is not None and not isinstance(evidence, Mapping):
                raise PlanError("plan item evidence must be an object")
            if status == "completed" and evidence is None and not item.evidence:
                raise PlanError(f"plan item {item_id} requires evidence before completion")
            evidence_values = item.evidence + ((dict(evidence),) if evidence else ())
            updated.append(replace(item, status=status, evidence=evidence_values))
        if not found:
            raise PlanError(f"unknown plan item: {item_id}")
        revised = replace(self, revision=self.revision + 1, items=tuple(updated), approved=self.approved if status not in {"failed", "blocked"} else False)
        revised.validate()
        return revised

    def approve_for_act(self, *, reason: str = "explicit user approval") -> "TaskPlan":
        if self.stale:
            raise PlanError("stale plan must be revised before Act approval")
        if not self.items:
            raise PlanError("cannot approve an empty plan")
        if any(item.status in {"failed", "blocked"} for item in self.items):
            raise PlanError("failed or blocked plan items require revision before approval")
        approved = replace(self, revision=self.revision + 1, mode="act", approved=True, approval_reason=reason[:500])
        approved.validate()
        return approved

    def evidence_fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def plan_from_response(payload: Any, *, task: str, rules_fingerprint: str = "", context_fingerprint: str = "") -> TaskPlan:
    """Parse only an explicit object/list plan protocol, never free-form prose."""
    if isinstance(payload, Mapping) and "plan" in payload:
        payload = payload["plan"]
    if isinstance(payload, list):
        payload = {"task": task, "items": payload}
    if not isinstance(payload, Mapping):
        raise PlanError("provider plan must be a JSON object or array")
    data = dict(payload)
    data.setdefault("task", task)
    data.setdefault("mode", "plan")
    data.setdefault("rules_fingerprint", rules_fingerprint)
    data.setdefault("context_fingerprint", context_fingerprint)
    return TaskPlan.from_dict(data)


def _reject_non_json(value: Any) -> Any:
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


__all__ = ["PLAN_SCHEMA_VERSION", "PLAN_STATUSES", "PlanError", "PlanItem", "TaskPlan", "plan_from_response"]
