"""Evidence-driven review and local security checks.

The review layer deliberately treats every input as untrusted data.  It does
not ask a model whether a change is safe: findings are produced by bounded,
deterministic checks and are joined with the append-only session and
transaction evidence.  The resulting document has a versioned, stable shape
that can be exported and verified later without exposing absolute paths or
raw session/backup contents.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping, Sequence
import uuid

from .context_policy import is_sensitive_context_path
from .security.json import bounded_json_loads
from .security.redaction import redact_text, redact_value
from .security.workspace import WorkspaceGuard, WorkspaceViolation, assert_no_path_alias
from .storage.session import SessionEvent, SessionFormatError, SessionStore
from .evaluation import evaluate_events
from .storage.transaction import (
    MAX_PREVIEW_CHARS,
    TransactionError,
    TransactionManifest,
    TransactionOperation,
    TransactionStore,
    _digest,
)


REVIEW_SCHEMA_VERSION = 1
ARTIFACT_SCHEMA_VERSION = 1
MAX_REVIEW_BYTES = 2_000_000
MAX_FILES = 512
MAX_FINDINGS = 512
MAX_HUNKS = 512
MAX_CHECKS = 32
MAX_EVIDENCE_REFS = 32
MAX_COMMAND_CHARS = 4_000
MAX_SCAN_FILE_BYTES = 2_000_000
MAX_SCAN_FILES = 2_000
MAX_DIFF_CHARS = 20_000

_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_STATUSES = {"pass", "fail", "skipped", "error"}
_CHECK_IDS = ("secrets", "forbidden_paths", "suspicious_commands", "syntax")
_PRIVATE_SCAN_DIRS = {
    ".git", ".forgecode", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", "tmp", "temp", "docs/goals", "tests",
}
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key material"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key-shaped value"),
    ("github_token", re.compile(r"\b(?:gh[ps]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "GitHub token-shaped value"),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API key-shaped value"),
    # Require a reasonably long literal.  This avoids flagging ordinary
    # source references such as ``api_key=os.getenv(...)`` while retaining
    # useful detection for pasted credentials (``token=abc...``).
    ("credential_assignment", re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie|authorization|token)\s*[:=]\s*[\"']?([A-Za-z0-9_./+=-]{12,})"), "credential assignment"),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "bearer credential"),
)
_SUSPICIOUS_REASONS = {
    "filesystem_destructive": "filesystem mutation or deletion",
    "privilege_or_system": "privilege or system access",
    "network_or_remote": "network, remote access, or dependency installation",
    "repository_irreversible": "repository history or state change",
}


class ReviewError(ValueError):
    """Review or review-artifact input is malformed, stale, or unsafe."""


class ReviewArtifactError(ReviewError):
    """An exported review artifact cannot be trusted for this workspace."""


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ReviewError(f"review value is not serializable: {type(exc).__name__}") from exc


def _sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _bounded_text(value: Any, limit: int = MAX_DIFF_CHARS) -> tuple[str, bool]:
    text = str(value or "")
    return (text[:limit] + ("\n[truncated]" if len(text) > limit else ""), len(text) > limit)


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2_000 or "\x00" in value or "\\" in value:
        return False
    if value.startswith("/") or value.endswith("/") or "//" in value:
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return not (len(parts[0]) >= 2 and parts[0][1] == ":")


def workspace_identity(guard: WorkspaceGuard) -> str:
    """Return a non-reversible workspace identity for artifact binding."""
    return _sha256(str(guard.root).encode("utf-8"))[:32]


def _event_payload(event: SessionEvent) -> dict[str, Any]:
    payload = event.payload
    return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class ReviewFinding:
    """One deterministic finding with references to bounded evidence."""

    finding_id: str
    severity: str
    source: str
    message: str
    path: str | None = None
    evidence_refs: tuple[str, ...] = ()
    line: int | None = None
    check_id: str | None = None

    def validate(self) -> None:
        if not isinstance(self.finding_id, str) or not re.fullmatch(r"F-[0-9a-f]{16}", self.finding_id):
            raise ReviewError("finding_id is invalid")
        if self.severity not in _SEVERITIES:
            raise ReviewError("finding severity is invalid")
        if not isinstance(self.source, str) or not self.source or len(self.source) > 128:
            raise ReviewError("finding source is invalid")
        if not isinstance(self.message, str) or not self.message or len(self.message) > 2_000:
            raise ReviewError("finding message is invalid")
        if self.path is not None and not _safe_relative(self.path):
            raise ReviewError("finding path is invalid")
        if self.line is not None and (isinstance(self.line, bool) or not isinstance(self.line, int) or self.line < 1 or self.line > 10_000_000):
            raise ReviewError("finding line is invalid")
        if self.check_id is not None and (not isinstance(self.check_id, str) or self.check_id not in _CHECK_IDS):
            raise ReviewError("finding check_id is invalid")
        if len(self.evidence_refs) > MAX_EVIDENCE_REFS or any(not isinstance(item, str) or len(item) > 256 for item in self.evidence_refs):
            raise ReviewError("finding evidence references are invalid")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "source": self.source,
            "message": self.message,
            "path": self.path,
            "evidence_refs": list(self.evidence_refs),
            "line": self.line,
            "check_id": self.check_id,
        }


@dataclass(frozen=True)
class SecurityCheckResult:
    """A built-in check outcome; model text is never an outcome source."""

    check_id: str
    status: str
    source: str
    severity: str
    message: str
    finding_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    scanned: int = 0
    omitted: int = 0
    duration_ms: int = 0
    budget: dict[str, int] = field(default_factory=dict)

    def validate(self) -> None:
        if self.check_id not in _CHECK_IDS:
            raise ReviewError("check_id is invalid")
        if self.status not in _STATUSES:
            raise ReviewError("check status is invalid")
        if not isinstance(self.source, str) or not self.source or len(self.source) > 128:
            raise ReviewError("check source is invalid")
        if self.severity not in _SEVERITIES:
            raise ReviewError("check severity is invalid")
        if not isinstance(self.message, str) or len(self.message) > 2_000:
            raise ReviewError("check message is invalid")
        for name, value in (("scanned", self.scanned), ("omitted", self.omitted), ("duration_ms", self.duration_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10_000_000:
                raise ReviewError(f"check {name} is invalid")
        if len(self.finding_ids) > MAX_FINDINGS or any(not isinstance(item, str) for item in self.finding_ids):
            raise ReviewError("check finding_ids are invalid")
        if len(self.evidence_refs) > MAX_EVIDENCE_REFS or any(not isinstance(item, str) or len(item) > 256 for item in self.evidence_refs):
            raise ReviewError("check evidence_refs are invalid")
        if not isinstance(self.budget, dict) or len(self.budget) > 16 or any(not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool) or v < 0 for k, v in self.budget.items()):
            raise ReviewError("check budget is invalid")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "check_id": self.check_id,
            "status": self.status,
            "source": self.source,
            "severity": self.severity,
            "message": self.message,
            "finding_ids": list(self.finding_ids),
            "evidence_refs": list(self.evidence_refs),
            "scanned": self.scanned,
            "omitted": self.omitted,
            "duration_ms": self.duration_ms,
            "budget": dict(sorted(self.budget.items())),
        }


@dataclass(frozen=True)
class DiffHunk:
    path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    current_sha256: str | None
    hunk: str
    truncated: bool = False
    binary: bool = False
    conflict: bool = False

    def validate(self) -> None:
        if not _safe_relative(self.path):
            raise ReviewError("diff path is invalid")
        if self.operation not in {"create", "update", "delete", "undo_create", "undo_update", "undo_delete"}:
            raise ReviewError("diff operation is invalid")
        for name, digest in (("before_sha256", self.before_sha256), ("after_sha256", self.after_sha256), ("current_sha256", self.current_sha256)):
            if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ReviewError(f"diff {name} is invalid")
        if not isinstance(self.hunk, str) or len(self.hunk) > MAX_DIFF_CHARS + 64:
            raise ReviewError("diff hunk is oversized")
        if not isinstance(self.truncated, bool) or not isinstance(self.binary, bool) or not isinstance(self.conflict, bool):
            raise ReviewError("diff flags are invalid")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ReviewReport:
    """Stable review document assembled from persisted evidence."""

    schema_version: int
    report_id: str
    generated_at: str
    workspace: str
    workspace_identity: str
    session: dict[str, Any]
    plan: dict[str, Any] | None
    references: dict[str, Any] | None
    context: dict[str, Any] | None
    transactions: tuple[dict[str, Any], ...]
    tests: tuple[dict[str, Any], ...]
    checks: tuple[SecurityCheckResult, ...]
    findings: tuple[ReviewFinding, ...]
    hunks: tuple[DiffHunk, ...]
    rollback: dict[str, Any]
    conflicts: tuple[str, ...]
    audit_complete: bool
    exit_code: int
    limits: dict[str, int]
    # Hook lifecycle evidence is additive so older reports remain readable.
    hooks: tuple[dict[str, Any], ...] = ()

    def validate(self) -> None:
        if self.schema_version != REVIEW_SCHEMA_VERSION:
            raise ReviewError("unsupported review schema")
        if not isinstance(self.report_id, str) or not re.fullmatch(r"[0-9a-f]{32}", self.report_id):
            raise ReviewError("report_id is invalid")
        if not isinstance(self.generated_at, str) or len(self.generated_at) > 128:
            raise ReviewError("generated_at is invalid")
        try:
            parsed = datetime.fromisoformat(self.generated_at)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
        except ValueError as exc:
            raise ReviewError("generated_at must include a timezone") from exc
        if self.workspace != "." or not re.fullmatch(r"[0-9a-f]{32}", self.workspace_identity):
            raise ReviewError("workspace identity is invalid")
        if not isinstance(self.session, dict) or len(self.session) > 32:
            raise ReviewError("session evidence is invalid")
        for label, value, allowed in (
            ("session", self.session, {"id", "run_id", "events", "sequence", "digest", "issues", "state", "trajectory"}),
            ("rollback", self.rollback, {"available", "transaction_id", "conflicts", "preview"}),
        ):
            if not isinstance(value, dict) or any(not isinstance(key, str) or key not in allowed for key in value):
                raise ReviewError(f"{label} evidence contains unknown fields")
        for item in self.transactions:
            if not isinstance(item, dict) or len(item) > 24:
                raise ReviewError("transaction evidence is invalid")
            allowed = {"transaction_id", "run_id", "created_at", "tool", "state", "operations", "preview", "approval", "plan_id", "plan_item_id", "verification", "parent_transaction_id", "rolled_back_by", "error", "schema_version"}
            if any(not isinstance(key, str) or key not in allowed for key in item):
                raise ReviewError("transaction evidence contains unknown fields")
        for item in self.tests:
            if not isinstance(item, dict) or len(item) > 20:
                raise ReviewError("test evidence is invalid")
            allowed = {"sequence", "kind", "outcome", "error_code", "attempt", "profile", "command", "exit_code", "timed_out", "ok", "verification"}
            if any(not isinstance(key, str) or key not in allowed for key in item):
                raise ReviewError("test evidence contains unknown fields")
        if not isinstance(self.hooks, tuple) or len(self.hooks) > MAX_FILES or any(not isinstance(item, dict) for item in self.hooks):
            raise ReviewError("hook evidence is invalid")
        for item in self.hooks:
            allowed = {"sequence", "kind", "event", "hook", "correlation_id", "blocked", "unresolved", "failure_policy", "error", "duration_seconds", "issues"}
            if any(not isinstance(key, str) or key not in allowed for key in item):
                raise ReviewError("hook evidence contains unknown fields")
            correlation = item.get("correlation_id")
            if correlation is not None and (not isinstance(correlation, str) or not correlation or len(correlation) > 256):
                raise ReviewError("hook correlation id is invalid")
            if "issues" in item and (not isinstance(item["issues"], list) or len(item["issues"]) > MAX_EVIDENCE_REFS):
                raise ReviewError("hook issues are invalid")
        for collection, maximum, label in ((self.transactions, MAX_FILES, "transactions"), (self.tests, MAX_FILES, "tests"), (self.checks, MAX_CHECKS, "checks"), (self.findings, MAX_FINDINGS, "findings"), (self.hunks, MAX_HUNKS, "hunks"), (self.conflicts, MAX_FINDINGS, "conflicts")):
            if len(collection) > maximum:
                raise ReviewError(f"too many {label}")
        for check in self.checks:
            check.validate()
        for finding in self.findings:
            finding.validate()
        for hunk in self.hunks:
            hunk.validate()
        if len(self.rollback) > 32:
            raise ReviewError("rollback evidence is invalid")
        if not isinstance(self.audit_complete, bool) or self.exit_code not in {0, 1, 2, 3}:
            raise ReviewError("review outcome is invalid")
        if not isinstance(self.limits, dict) or any(not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, int) or v < 0 for k, v in self.limits.items()):
            raise ReviewError("review limits are invalid")
        encoded = _canonical_json(self.to_dict(include_digest=False))
        if len(encoded) > MAX_REVIEW_BYTES:
            raise ReviewError("review report exceeds size limit")

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        checks = [item.to_dict() for item in sorted(self.checks, key=lambda item: item.check_id)]
        findings = [item.to_dict() for item in sorted(self.findings, key=lambda item: (item.path or "", item.severity, item.finding_id))]
        hunks = [item.to_dict() for item in sorted(self.hunks, key=lambda item: (item.path, item.operation))]
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "workspace": self.workspace,
            "workspace_identity": self.workspace_identity,
            "session": self.session,
            "plan": self.plan,
            "references": self.references,
            "context": self.context,
            "transactions": list(self.transactions),
            "tests": list(self.tests),
            "checks": checks,
            "findings": findings,
            "hunks": hunks,
            "rollback": self.rollback,
            "conflicts": list(self.conflicts),
            "audit_complete": self.audit_complete,
            "exit_code": self.exit_code,
            "limits": dict(sorted(self.limits.items())),
            "hooks": [redact_value(item) for item in self.hooks],
        }
        if include_digest:
            payload["report_sha256"] = _sha256(_canonical_json(payload))
        return payload

    def to_json(self) -> str:
        self.validate()
        return _canonical_json(self.to_dict()).decode("utf-8")


def _finding(check_id: str, severity: str, source: str, message: str, *, path: str | None = None, line: int | None = None, evidence_refs: Iterable[str] = ()) -> ReviewFinding:
    refs = tuple(sorted({str(item)[:256] for item in evidence_refs if item}))[:MAX_EVIDENCE_REFS]
    token = "\0".join((check_id, severity, source, path or "", str(line or 0), message[:500], *refs))
    return ReviewFinding(f"F-{_sha256(token)[:16]}", severity, source, message[:2_000], path, refs, line, check_id)


def _session_digest(path: Path) -> str | None:
    try:
        assert_no_path_alias(path)
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, getattr(before, "st_ino", 0)) != (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", 0)):
            return None
        assert_no_path_alias(path)
        return _sha256(raw)
    except (OSError, WorkspaceViolation):
        return None


def _safe_session_summary(store: SessionStore, result_events: Sequence[SessionEvent], issues: Sequence[Any]) -> dict[str, Any]:
    run_id = result_events[0].run_id if result_events else store.run_id
    # Prefer an explicit lifecycle state over a terminal ``stopped_reason``.
    # The latter is an outcome label (for example ``model_finished``), not a
    # RunState and must not obscure whether the durable session reached
    # ``completed`` or ``recovery_required``.
    state = None
    outcome_label = None
    for event in result_events:
        payload = _event_payload(event)
        if event.kind == "state_transition":
            candidate = payload.get("to")
            if isinstance(candidate, str) and candidate:
                state = candidate[:128]
        elif event.kind == "checkpoint":
            candidate = payload.get("state")
            if isinstance(candidate, str) and candidate:
                state = candidate[:128]
        elif event.kind == "final":
            candidate = payload.get("stopped_reason")
            if isinstance(candidate, str) and candidate:
                outcome_label = candidate[:128]
    if state is None:
        state = outcome_label
    return {
        "id": store.path.stem,
        "run_id": run_id,
        "events": len(result_events),
        "sequence": result_events[-1].sequence if result_events else 0,
        "digest": _session_digest(store.path),
        "issues": [{"line": getattr(issue, "line", 0), "message": str(getattr(issue, "message", issue))[:500]} for issue in issues[:100]],
        "state": str(state)[:128] if state is not None else None,
        "trajectory": evaluate_events(result_events).to_dict(),
    }


def _latest_event_payload(events: Sequence[SessionEvent], kinds: set[str]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.kind in kinds:
            payload = _event_payload(event)
            return redact_value(payload)
    return None


def _event_tests(events: Sequence[SessionEvent]) -> tuple[dict[str, Any], ...]:
    tests: list[dict[str, Any]] = []
    for event in events:
        if event.kind not in {"verification_result", "transaction_verification", "command_result", "command_timeout", "command_refusal", "test_result", "test_started", "test_finished", "test_profile_result"}:
            continue
        payload = _event_payload(event)
        # Keep output and command evidence bounded; never infer success from
        # model prose or an untyped ``ok`` field in a final response.
        verification = payload.get("verification") if isinstance(payload.get("verification"), dict) else None
        if verification is None and isinstance(payload.get("result"), dict):
            verification = payload.get("result")
        if verification is None and isinstance(payload.get("evidence"), dict):
            verification = payload.get("evidence")
        command_value = payload.get("command")
        if not isinstance(command_value, str) and isinstance(verification, dict):
            command_value = verification.get("command", "")
        exit_value = payload.get("exit_code")
        if exit_value is None and isinstance(verification, dict):
            exit_value = verification.get("exit_code")
        timed_value = payload.get("timed_out", False)
        if not isinstance(timed_value, bool) and isinstance(verification, dict):
            timed_value = verification.get("timed_out", False)
        ok_value = payload.get("ok", False)
        if not isinstance(ok_value, bool) and isinstance(verification, dict):
            ok_value = verification.get("ok", False)
        item = {
            "sequence": event.sequence,
            "kind": event.kind,
            "outcome": event.outcome,
            "error_code": event.error_code,
            "attempt": payload.get("attempt"),
            "profile": payload.get("profile") or payload.get("profile_name"),
            "command": redact_text(str(command_value or ""))[:MAX_COMMAND_CHARS],
            "exit_code": exit_value if isinstance(exit_value, int) and not isinstance(exit_value, bool) else None,
            "timed_out": bool(timed_value) if isinstance(timed_value, bool) else False,
            "ok": bool(ok_value) if isinstance(ok_value, bool) else False,
            "verification": redact_value(verification) if isinstance(verification, dict) else None,
        }
        tests.append(item)
    tests.sort(key=lambda item: (int(item.get("sequence") or 0), str(item.get("kind"))))
    return tuple(tests[-MAX_FILES:])


def _event_hooks(events: Sequence[SessionEvent], *, secrets: Iterable[str] = ()) -> tuple[dict[str, Any], ...]:
    """Normalize hook lifecycle events into bounded review evidence.

    Applications historically wrapped the event payload as ``{"event": ...}``
    while newer callers may persist a direct hook record.  Accept both shapes,
    but retain only deterministic metadata and redact nested values so a hook
    cannot smuggle credentials into an exported report.
    """
    result: list[dict[str, Any]] = []
    for event in events:
        if event.kind not in {"hook_event", "hook_issue", "hook_cleanup", "hook_result"}:
            continue
        payload = _event_payload(event)
        nested = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        if not isinstance(nested, dict):
            continue
        issues = nested.get("issues")
        if not isinstance(issues, list):
            issues = nested.get("hook_issues") if isinstance(nested.get("hook_issues"), list) else []
        safe_issues: list[dict[str, Any]] = []
        for raw_issue in issues[:MAX_EVIDENCE_REFS]:
            if not isinstance(raw_issue, dict):
                continue
            safe_issue = {
                key: redact_value(value, secrets)
                for key, value in raw_issue.items()
                if key in {"hook", "event", "error", "blocked", "unresolved", "correlation_id", "failure_policy", "duration_seconds"}
            }
            safe_issues.append(safe_issue)
        correlation = nested.get("correlation_id") or payload.get("correlation_id")
        record = {
            "sequence": event.sequence,
            "kind": event.kind,
            "event": str(nested.get("event") or event.kind)[:128],
            "hook": str(nested.get("hook") or "")[:128],
            "correlation_id": str(correlation)[:256] if correlation is not None else "",
            "blocked": bool(nested.get("blocked", False)),
            "unresolved": bool(nested.get("unresolved", False)),
            "failure_policy": str(nested.get("failure_policy") or "observe_only")[:32],
            "error": redact_text(str(nested.get("error") or ""), secrets)[:512],
            "duration_seconds": float(nested.get("duration_seconds") or 0.0) if isinstance(nested.get("duration_seconds", 0.0), (int, float)) and math.isfinite(float(nested.get("duration_seconds") or 0.0)) else 0.0,
            "issues": safe_issues,
        }
        result.append(record)
    result.sort(key=lambda item: (int(item.get("sequence") or 0), item.get("correlation_id", ""), item.get("event", "")))
    return tuple(result[-MAX_FILES:])


def _current_file_bytes(guard: WorkspaceGuard, relative: str) -> bytes | None:
    try:
        path = guard.resolve(relative)
        if not path.exists():
            return None
        if not path.is_file():
            return None
        assert_no_path_alias(path)
        stat_before = path.stat()
        if stat_before.st_size > MAX_SCAN_FILE_BYTES:
            return None
        value = path.read_bytes()
        stat_after = path.stat()
        assert_no_path_alias(path)
        if (stat_before.st_size, stat_before.st_mtime_ns, getattr(stat_before, "st_ino", 0)) != (stat_after.st_size, stat_after.st_mtime_ns, getattr(stat_after, "st_ino", 0)):
            raise ReviewError(f"file changed while it was read: {relative}")
        return value
    except (OSError, ValueError, WorkspaceViolation) as exc:
        raise ReviewError(f"cannot read review file {relative}: {type(exc).__name__}") from exc


def _decode_diff(before: bytes | None, after: bytes | None, path: str, operation: str, before_hash: str | None, after_hash: str | None, current_hash: str | None, *, secrets: Iterable[str]) -> DiffHunk:
    binary = any(value is not None and b"\x00" in value for value in (before, after))
    conflict = current_hash != after_hash
    if binary:
        text = "[binary diff omitted; hashes retained]"
        truncated = False
    else:
        try:
            before_text = (before or b"").decode("utf-8")
            after_text = (after or b"").decode("utf-8")
            lines = difflib.unified_diff(
                before_text.splitlines(), after_text.splitlines(),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
            )
            text = "\n".join(redact_text(line, secrets) for line in lines)
        except UnicodeDecodeError:
            binary = True
            text = "[non-UTF-8 diff omitted; hashes retained]"
        text, truncated = _bounded_text(text, MAX_DIFF_CHARS)
    return DiffHunk(path, operation, before_hash, after_hash, current_hash, text, truncated, binary, conflict)


def _manifest_hunks(guard: WorkspaceGuard, store: TransactionStore, manifest: TransactionManifest, *, secrets: Iterable[str]) -> tuple[DiffHunk, ...]:
    result: list[DiffHunk] = []
    for operation in sorted(manifest.operations, key=lambda item: (item.path, item.operation)):
        before = None
        if operation.backup_sha256:
            try:
                before = store._read_blob(operation.backup_sha256)
            except TransactionError:
                before = None
        current = _current_file_bytes(guard, operation.path)
        current_hash = _sha256(current) if current is not None else None
        # For a delete, the expected after state is absence; for a create the
        # backup is absent.  Current bytes are the strongest available proof
        # and are intentionally not trusted as a pass if hashes disagree.
        result.append(_decode_diff(before, current, operation.path, operation.operation, operation.before_sha256, operation.after_sha256, current_hash, secrets=secrets))
    return tuple(result)


def _scan_paths(guard: WorkspaceGuard, *, max_files: int) -> tuple[list[tuple[str, Path]], int]:
    candidates: list[tuple[str, Path]] = []
    omitted = 0
    try:
        for directory, names, filenames in os.walk(guard.root, topdown=True, followlinks=False):
            relative_dir = Path(directory).relative_to(guard.root).as_posix()
            if relative_dir == ".":
                relative_dir = ""
            names[:] = [name for name in sorted(names, key=str.lower) if (relative_dir + "/" + name if relative_dir else name) not in _PRIVATE_SCAN_DIRS and name not in {".git", ".forgecode", ".venv", "node_modules", "__pycache__", ".pytest_cache", "tmp", "temp"}]
            for name in sorted(filenames, key=str.lower):
                path = Path(directory) / name
                try:
                    # Resolve and alias-check each candidate independently.
                    # ``os.walk`` gives us names, not a stable security
                    # decision; a raced symlink must be omitted rather than
                    # followed by a later scanner.
                    resolved = guard.resolve(path, must_exist=True)
                    assert_no_path_alias(resolved)
                    if resolved != path.absolute():
                        raise WorkspaceViolation("scan candidate is an alias")
                    relative = guard.relative(resolved)
                except (OSError, ValueError, WorkspaceViolation):
                    omitted += 1
                    continue
                if len(candidates) >= max_files:
                    omitted += 1
                    continue
                try:
                    if not resolved.is_file() or resolved.stat().st_size > MAX_SCAN_FILE_BYTES:
                        omitted += 1
                        continue
                    candidates.append((relative, resolved))
                except OSError:
                    omitted += 1
    except OSError:
        omitted += 1
    candidates.sort(key=lambda item: item[0])
    return candidates, omitted


def _run_secrets_check(guard: WorkspaceGuard, *, max_files: int, secrets: Iterable[str]) -> tuple[SecurityCheckResult, list[ReviewFinding]]:
    started = time.monotonic()
    paths, omitted = _scan_paths(guard, max_files=max_files)
    findings: list[ReviewFinding] = []
    secret_values = tuple(item for item in secrets if isinstance(item, str) and item)
    for relative, path in paths:
        try:
            raw = _current_file_bytes(guard, relative)
            if raw is None:
                omitted += 1
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError, ReviewError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for pattern_id, pattern, label in _SECRET_PATTERNS:
                match = pattern.search(line)
                # Common placeholders and code expressions are not secret
                # material.  The check remains heuristic but is intentionally
                # conservative about findings that would make every source
                # file fail review.
                literal = match.group(1) if match and match.lastindex else ""
                lowered_literal = literal.lower()
                placeholder = lowered_literal in {"os.getenv", "getenv", "environment", "your-key", "your_key", "fake-key", "do-not-store", "do-not-read"}
                remainder = line[match.end():].lstrip() if match else ""
                expression = (
                    lowered_literal.startswith(("os.", "self.", "args.", "config.", "effective.", "str.", "get_"))
                    or "." in literal
                    or " or " in remainder
                    or remainder.startswith("or ")
                    or remainder.startswith(("(", "["))
                )
                likely_secret = bool(match and not placeholder and not expression)
                if likely_secret or any(value and value in line for value in secret_values):
                    evidence = f"file:{relative}#L{number}:{pattern_id}"
                    findings.append(_finding("secrets", "critical" if pattern_id == "private_key" else "high", "static.security", f"possible {label}; value redacted", path=relative, line=number, evidence_refs=(evidence,)))
                    break
            if len(findings) >= MAX_FINDINGS:
                omitted += 1
                break
        if len(findings) >= MAX_FINDINGS:
            break
    status = "fail" if findings else "pass"
    message = f"scanned {len(paths)} text files; {len(findings)} potential secret finding(s)" if paths else "no readable files to scan"
    check = SecurityCheckResult("secrets", status, "static.security", "high" if findings else "info", message, tuple(item.finding_id for item in findings), tuple(ref for item in findings for ref in item.evidence_refs)[:MAX_EVIDENCE_REFS], len(paths), omitted, int((time.monotonic() - started) * 1000), {"max_files": max_files, "max_file_bytes": MAX_SCAN_FILE_BYTES})
    return check, findings


def _run_forbidden_paths_check(guard: WorkspaceGuard, operations: Sequence[TransactionOperation], *, max_files: int) -> tuple[SecurityCheckResult, list[ReviewFinding]]:
    started = time.monotonic()
    findings: list[ReviewFinding] = []
    seen: set[str] = set()
    for operation in sorted(operations, key=lambda item: (item.path, item.operation))[:max_files]:
        if operation.path in seen:
            continue
        seen.add(operation.path)
        if not _safe_relative(operation.path):
            findings.append(_finding("forbidden_paths", "critical", "static.security", "transaction path is not a canonical workspace-relative path", path=None, evidence_refs=(f"transaction-path:{operation.path[:200]}",)))
        elif is_sensitive_context_path(operation.path):
            findings.append(_finding("forbidden_paths", "high", "static.security", "transaction targets a sensitive or runtime path", path=operation.path, evidence_refs=(f"transaction-path:{operation.path}",)))
        else:
            try:
                resolved = guard.resolve(operation.path)
                if not resolved.is_relative_to(guard.root):
                    raise WorkspaceViolation("outside workspace")
            except (OSError, ValueError, WorkspaceViolation):
                findings.append(_finding("forbidden_paths", "critical", "static.security", "transaction path cannot be resolved inside the workspace", path=operation.path if _safe_relative(operation.path) else None, evidence_refs=(f"transaction-path:{operation.path[:200]}",)))
    status = "fail" if findings else ("skipped" if not operations else "pass")
    message = "sensitive or unsafe transaction paths found" if findings else (f"checked {len(seen)} transaction path(s)" if seen else "no transaction paths supplied")
    check = SecurityCheckResult("forbidden_paths", status, "static.security", "high" if findings else "info", message, tuple(item.finding_id for item in findings), tuple(ref for item in findings for ref in item.evidence_refs)[:MAX_EVIDENCE_REFS], len(seen), max(0, len(operations) - len(seen)), int((time.monotonic() - started) * 1000), {"max_files": max_files})
    return check, findings


def _run_suspicious_commands(events: Sequence[SessionEvent], *, max_commands: int) -> tuple[SecurityCheckResult, list[ReviewFinding]]:
    from .tools.shell import classify_command

    started = time.monotonic()
    findings: list[ReviewFinding] = []
    commands: list[tuple[int, str, dict[str, Any]]] = []
    for event in events:
        payload = _event_payload(event)
        command = payload.get("command")
        if not isinstance(command, str) and event.kind == "tool_call":
            arguments = payload.get("arguments")
            command = arguments.get("command") if isinstance(arguments, dict) else None
        if isinstance(command, str) and command.strip():
            commands.append((event.sequence, command[:MAX_COMMAND_CHARS], payload))
    commands = sorted(commands, key=lambda item: (item[0], item[1]))[-max_commands:]
    for sequence, command, payload in commands:
        risk, reasons, hard_blocked = classify_command(command)
        if risk != "normal" or hard_blocked:
            severity = "critical" if hard_blocked else ("high" if risk in {"privilege_or_system", "repository_irreversible"} else "medium")
            reason = "; ".join(reasons) or _SUSPICIOUS_REASONS.get(risk, risk)
            findings.append(_finding("suspicious_commands", severity, "command.policy", f"suspicious command observed ({reason})", evidence_refs=(f"session-seq:{sequence}",)))
    if not commands:
        status, severity, message = "skipped", "info", "no command evidence was recorded"
    elif findings:
        status, severity, message = "fail", max((item.severity for item in findings), key=lambda value: (len(value), value)), f"{len(findings)} suspicious command(s) require review"
    else:
        status, severity, message = "pass", "info", f"checked {len(commands)} command event(s); no suspicious patterns"
    check = SecurityCheckResult("suspicious_commands", status, "command.policy", severity, message, tuple(item.finding_id for item in findings), tuple(ref for item in findings for ref in item.evidence_refs)[:MAX_EVIDENCE_REFS], len(commands), 0, int((time.monotonic() - started) * 1000), {"max_commands": max_commands, "max_command_chars": MAX_COMMAND_CHARS})
    return check, findings


def _run_syntax_check(guard: WorkspaceGuard, paths: Sequence[str], *, max_files: int) -> tuple[SecurityCheckResult, list[ReviewFinding]]:
    started = time.monotonic()
    # A report with no transaction (for example ``forgecode review`` on a
    # freshly edited workspace) still needs a real compile/syntax signal.  If
    # callers did not provide changed paths, discover bounded workspace Python
    # files through the same exclusion/alias-aware scanner used by the secret
    # check.  Transaction-scoped reviews keep the narrower changed-file set.
    omitted = 0
    if paths:
        raw_candidates = [path for path in paths if isinstance(path, str) and path.lower().endswith(".py") and _safe_relative(path)]
        omitted = max(0, len(raw_candidates) - max_files)
    else:
        discovered, omitted = _scan_paths(guard, max_files=max_files)
        raw_candidates = [relative for relative, _path in discovered if relative.lower().endswith(".py")]
    candidates = sorted(set(raw_candidates))[:max_files]
    findings: list[ReviewFinding] = []
    for relative in candidates:
        try:
            raw = _current_file_bytes(guard, relative)
            if raw is None:
                continue
            ast.parse(raw.decode("utf-8"), filename=relative)
        except SyntaxError as exc:
            line = int(exc.lineno or 1)
            findings.append(_finding("syntax", "high", "python.ast", f"Python syntax error: {str(exc.msg)[:500]}", path=relative, line=line, evidence_refs=(f"file:{relative}#L{line}",)))
        except (UnicodeDecodeError, ReviewError) as exc:
            findings.append(_finding("syntax", "medium", "python.ast", f"Python source could not be compiled: {type(exc).__name__}", path=relative, evidence_refs=(f"file:{relative}",)))
        except Exception as exc:
            findings.append(_finding("syntax", "medium", "python.ast", f"Python syntax check error: {type(exc).__name__}", path=relative, evidence_refs=(f"file:{relative}",)))
    if not candidates:
        status, severity, message = "skipped", "info", "no Python files were selected for syntax checking"
    elif findings:
        status, severity, message = "fail", "high", f"{len(findings)} Python syntax/compile finding(s)"
    else:
        status, severity, message = "pass", "info", f"parsed {len(candidates)} Python file(s) successfully"
    check = SecurityCheckResult("syntax", status, "python.ast", severity, message, tuple(item.finding_id for item in findings), tuple(ref for item in findings for ref in item.evidence_refs)[:MAX_EVIDENCE_REFS], len(candidates), omitted, int((time.monotonic() - started) * 1000), {"max_files": max_files})
    return check, findings


def run_security_checks(
    guard: WorkspaceGuard,
    *,
    events: Sequence[SessionEvent] = (),
    operations: Sequence[TransactionOperation] = (),
    changed_paths: Sequence[str] = (),
    secrets: Iterable[str] = (),
    max_files: int = 256,
    max_commands: int = 128,
) -> tuple[tuple[SecurityCheckResult, ...], tuple[ReviewFinding, ...]]:
    """Run all built-in checks with explicit bounded budgets."""
    if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= MAX_SCAN_FILES:
        raise ReviewError("max_files must be between 1 and 2000")
    if isinstance(max_commands, bool) or not isinstance(max_commands, int) or not 1 <= max_commands <= 1_000:
        raise ReviewError("max_commands must be between 1 and 1000")
    checks: list[SecurityCheckResult] = []
    findings: list[ReviewFinding] = []
    for runner in (
        lambda: _run_secrets_check(guard, max_files=max_files, secrets=secrets),
        lambda: _run_forbidden_paths_check(guard, operations, max_files=max_files),
        lambda: _run_suspicious_commands(events, max_commands=max_commands),
        lambda: _run_syntax_check(guard, changed_paths, max_files=max_files),
    ):
        try:
            check, found = runner()
        except Exception as exc:
            check_id = _CHECK_IDS[len(checks)]
            check = SecurityCheckResult(check_id, "error", "static.security", "high", f"check failed: {type(exc).__name__}", (), (), 0, 0, 0, {"max_files": max_files})
            found = []
        checks.append(check)
        findings.extend(found)
        if len(findings) >= MAX_FINDINGS:
            findings = findings[:MAX_FINDINGS]
            break
    checks.sort(key=lambda item: item.check_id)
    findings.sort(key=lambda item: (item.path or "", item.line or 0, item.severity, item.finding_id))
    return tuple(checks), tuple(findings)


def _manifest_summary(manifest: TransactionManifest) -> dict[str, Any]:
    payload = manifest.to_dict()
    # The exact backup bytes are deliberately absent.  Keep verification
    # metadata bounded and preserve hash/conflict state for audit consumers.
    payload["operations"] = [
        {
            "path": operation.path,
            "operation": operation.operation,
            "before_sha256": operation.before_sha256,
            "after_sha256": operation.after_sha256,
            "before_bytes": operation.before_bytes,
            "after_bytes": operation.after_bytes,
        }
        for operation in manifest.operations
    ]
    payload["preview"] = redact_text(payload.get("preview", ""))[:MAX_PREVIEW_CHARS]
    if isinstance(payload.get("verification"), dict):
        payload["verification"] = redact_value(payload["verification"])
    return payload


class ReviewBuilder:
    """Build a deterministic review from one workspace's durable evidence."""

    def __init__(self, guard: WorkspaceGuard, *, transaction_store: TransactionStore | None = None, max_files: int = 256, max_commands: int = 128, secrets: Iterable[str] = ()):
        self.guard = guard
        self.transaction_store = transaction_store or TransactionStore(guard)
        if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= MAX_SCAN_FILES:
            raise ReviewError("max_files must be between 1 and 2000")
        if isinstance(max_commands, bool) or not isinstance(max_commands, int) or not 1 <= max_commands <= 1_000:
            raise ReviewError("max_commands must be between 1 and 1000")
        self.max_files = max_files
        self.max_commands = max_commands
        self.secrets = tuple(item for item in secrets if isinstance(item, str) and item)
        self._session_selection_issues: list[str] = []

    def _session_path(self, session: Path | str | None) -> Path | None:
        self._session_selection_issues = []
        if session is None:
            try:
                directory = self.guard.resolve(Path(".forgecode") / "sessions")
                assert_no_path_alias(directory)
            except (OSError, ValueError, WorkspaceViolation) as exc:
                self._session_selection_issues.append(f"session directory unavailable: {type(exc).__name__}")
                return None
            if not directory.is_dir():
                return None
            candidates: list[tuple[int, Path]] = []
            try:
                raw_candidates = directory.glob("*.jsonl")
                assert_no_path_alias(directory)
                for candidate in raw_candidates:
                    try:
                        resolved = self.guard.resolve(candidate, must_exist=True)
                        assert_no_path_alias(resolved)
                        if resolved != candidate.absolute() or resolved.parent != directory or not resolved.is_file():
                            self._session_selection_issues.append(f"session candidate omitted: {candidate.name[:128]}")
                            continue
                        candidates.append((resolved.stat().st_mtime_ns, resolved))
                    except (OSError, ValueError, WorkspaceViolation) as exc:
                        self._session_selection_issues.append(f"session candidate omitted: {candidate.name[:128]} ({type(exc).__name__})")
                        continue
            except OSError:
                self._session_selection_issues.append("session directory could not be enumerated")
                return None
            candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
            return candidates[0][1] if candidates else None
        path = self.guard.resolve(session)
        if path.suffix.lower() != ".jsonl" or path.parent != self.guard.resolve(Path(".forgecode") / "sessions"):
            raise ReviewError("session must be a JSONL file under .forgecode/sessions")
        try:
            assert_no_path_alias(path)
        except WorkspaceViolation as exc:
            raise ReviewError("session path is a symlink or junction alias") from exc
        return path

    def build(self, *, session: Path | str | None = None, transaction_id: str = "latest") -> ReviewReport:
        try:
            session_path = self._session_path(session)
        except (OSError, ValueError, WorkspaceViolation) as exc:
            session_path = None
            self._session_selection_issues.append(f"session selection failed: {type(exc).__name__}")
        events: tuple[SessionEvent, ...] = ()
        session_issues: tuple[Any, ...] = tuple(self._session_selection_issues)
        session_summary: dict[str, Any] = {"id": None, "run_id": None, "events": 0, "sequence": 0, "digest": None, "issues": [], "state": None}
        if session_path is not None:
            try:
                store = SessionStore(session_path, secrets=self.secrets)
                read = store.read_with_issues()
                events, session_issues = tuple(read.events), tuple(self._session_selection_issues) + tuple(read.issues)
                session_summary = _safe_session_summary(store, events, session_issues)
            except (OSError, ValueError, SessionFormatError, WorkspaceViolation) as exc:
                # A candidate can be replaced between enumeration and open.
                # Preserve a review-shaped diagnostic instead of crashing or
                # treating an unreadable audit stream as an empty success.
                session_issues = tuple(self._session_selection_issues) + (f"session unavailable: {type(exc).__name__}",)
                session_summary = {
                    "id": session_path.stem,
                    "run_id": None,
                    "events": 0,
                    "sequence": 0,
                    "digest": None,
                    "issues": [{"line": 0, "message": session_issues[0]}],
                    "state": None,
                }
        try:
            manifests = self.transaction_store.list(limit=min(MAX_FILES, 1_000))
            ledger_issues = self.transaction_store.last_list_issues
        except Exception as exc:
            manifests, ledger_issues = (), (f"transaction ledger unavailable: {type(exc).__name__}",)
        selected: TransactionManifest | None = None
        if transaction_id == "latest":
            selected = next((item for item in manifests if item.state in {"committed", "failed", "recovery_required", "undone", "prepared"}), None)
        elif transaction_id:
            selected = next((item for item in manifests if item.transaction_id == transaction_id), None)
            if selected is None:
                try:
                    selected = self.transaction_store.load(transaction_id)
                except TransactionError as exc:
                    ledger_issues = tuple(ledger_issues) + (str(exc)[:500],)
        transaction_summaries = tuple(_manifest_summary(item) for item in sorted(manifests, key=lambda item: (item.created_at, item.transaction_id))[-MAX_FILES:])
        selected_manifests = [selected] if selected is not None else list(manifests)
        operations = tuple(operation for item in selected_manifests for operation in item.operations)
        changed_paths = tuple(sorted({operation.path for operation in operations}))
        checks, findings = run_security_checks(self.guard, events=events, operations=operations, changed_paths=changed_paths, secrets=self.secrets, max_files=self.max_files, max_commands=self.max_commands)
        hunks: list[DiffHunk] = []
        for item in selected_manifests[:MAX_FILES]:
            try:
                hunks.extend(_manifest_hunks(self.guard, self.transaction_store, item, secrets=self.secrets))
            except Exception:
                # Missing/corrupt blobs are represented by transaction conflicts
                # and a bounded empty hunk rather than a false successful diff.
                for operation in item.operations:
                    hunks.append(DiffHunk(operation.path, operation.operation, operation.before_sha256, operation.after_sha256, None, "[diff unavailable; hash evidence could not be read]", False, False, True))
        rollback: dict[str, Any] = {"available": False, "transaction_id": selected.transaction_id if selected else None, "conflicts": []}
        conflicts: list[str] = [str(item)[:500] for item in ledger_issues]
        if selected is not None:
            try:
                preview = self.transaction_store.preview_undo(selected.transaction_id)
                rollback = {"available": preview.available, "transaction_id": selected.transaction_id, "conflicts": list(preview.conflicts)[:MAX_EVIDENCE_REFS], "preview": preview.preview[:MAX_PREVIEW_CHARS]}
                conflicts.extend(preview.conflicts)
            except TransactionError as exc:
                conflicts.append(str(exc)[:500])
        if any(item.conflict for item in hunks):
            conflicts.append("one or more transaction after hashes differ from current files")
        conflicts.extend(str(item.get("message", ""))[:500] for item in session_summary.get("issues", []) if item.get("message"))
        plan = _latest_event_payload(events, {"plan_created", "plan_updated"})
        references = _latest_event_payload(events, {"references_resolved"})
        context = _latest_event_payload(events, {"context_index", "repository_snapshot"})
        tests = _event_tests(events)
        hooks = _event_hooks(events, secrets=self.secrets)
        # Hook issues are evidence, not prose.  A blocked, unresolved or
        # fail-closed error makes the review require recovery even when the
        # underlying tool happened to return a successful result.
        for hook_record in hooks:
            if hook_record.get("blocked") or hook_record.get("unresolved"):
                conflicts.append(f"hook lifecycle issue ({hook_record.get('correlation_id') or 'unknown'})")
            for issue in hook_record.get("issues", ()):
                if isinstance(issue, dict) and (issue.get("blocked") or issue.get("unresolved") or issue.get("failure_policy") == "fail_closed"):
                    conflicts.append(f"hook issue {issue.get('hook') or 'unknown'} ({issue.get('correlation_id') or hook_record.get('correlation_id') or 'unknown'})")
        conflicts = sorted(set(item for item in conflicts if item))[:MAX_FINDINGS]
        # A review can pass only if durable input is valid, all applicable
        # built-in checks pass, and there are no transaction/session conflicts.
        audit_complete = not session_issues and not ledger_issues and not conflicts
        check_failure = any(item.status in {"fail", "error"} for item in checks)
        exit_code = 3 if conflicts else (1 if check_failure or not audit_complete else 0)
        report = ReviewReport(
            REVIEW_SCHEMA_VERSION,
            uuid.uuid4().hex,
            datetime.now(timezone.utc).isoformat(),
            ".",
            workspace_identity(self.guard),
            session_summary,
            plan,
            references,
            context,
            transaction_summaries,
            tests,
            checks,
            findings,
            tuple(sorted(hunks, key=lambda item: (item.path, item.operation)))[:MAX_HUNKS],
            rollback,
            tuple(conflicts),
            audit_complete,
            exit_code,
            {"max_files": self.max_files, "max_commands": self.max_commands, "max_review_bytes": MAX_REVIEW_BYTES, "max_findings": MAX_FINDINGS, "max_hunks": MAX_HUNKS},
            hooks,
        )
        report.validate()
        return report


def _validate_report_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReviewArtifactError("review report must be an object")
    if raw.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ReviewArtifactError("unsupported review schema")
    unknown = set(raw) - {
        "schema_version", "report_id", "generated_at", "workspace", "workspace_identity", "session", "plan", "references", "context", "transactions", "tests", "hooks", "checks", "findings", "hunks", "rollback", "conflicts", "audit_complete", "exit_code", "limits", "report_sha256",
    }
    if unknown:
        raise ReviewArtifactError("review report contains unknown fields")
    supplied = raw.get("report_sha256")
    unsigned = {key: value for key, value in raw.items() if key != "report_sha256"}
    if not isinstance(supplied, str) or not re.fullmatch(r"[0-9a-f]{64}", supplied) or _sha256(_canonical_json(unsigned)) != supplied:
        raise ReviewArtifactError("review report digest mismatch")
    return dict(raw)


def _report_from_dict(raw: Mapping[str, Any]) -> ReviewReport:
    data = _validate_report_dict(raw)
    try:
        checks = tuple(SecurityCheckResult(**{**item, "finding_ids": tuple(item.get("finding_ids", ())), "evidence_refs": tuple(item.get("evidence_refs", ()))}) for item in data["checks"])
        findings = tuple(ReviewFinding(**{**item, "evidence_refs": tuple(item.get("evidence_refs", ()))}) for item in data["findings"])
        hunks = tuple(DiffHunk(**item) for item in data["hunks"])
        report = ReviewReport(
            data["schema_version"], data["report_id"], data["generated_at"], data["workspace"], data["workspace_identity"], data["session"], data.get("plan"), data.get("references"), data.get("context"), tuple(data["transactions"]), tuple(data["tests"]), checks, findings, hunks, data["rollback"], tuple(data["conflicts"]), data["audit_complete"], data["exit_code"], data["limits"], tuple(data.get("hooks", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewArtifactError(f"invalid review report fields: {type(exc).__name__}") from exc
    report.validate()
    return report


def export_review(report: ReviewReport, path: Path | str, guard: WorkspaceGuard, *, file_digests: Mapping[str, str] | None = None) -> Path:
    """Write a bounded, workspace-bound review artifact atomically."""
    report.validate()
    try:
        destination = guard.resolve(path)
    except (OSError, ValueError, WorkspaceViolation) as exc:
        raise ReviewArtifactError("review artifact path is outside workspace") from exc
    if destination == guard.root or destination.suffix.lower() not in {".json", ".review"}:
        raise ReviewArtifactError("review artifact must be a JSON file inside the workspace")
    try:
        assert_no_path_alias(destination)
    except WorkspaceViolation as exc:
        raise ReviewArtifactError(str(exc)) from exc
    digest_map: dict[str, str] = {}
    if file_digests is None:
        for hunk in report.hunks:
            if hunk.current_sha256:
                digest_map[hunk.path] = hunk.current_sha256
    else:
        for relative, digest in file_digests.items():
            # ``null`` is the explicit, signed representation of an absent
            # file (for a delete transaction); a string must be a SHA-256.
            if not _safe_relative(relative) or (digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest))):
                raise ReviewArtifactError("file digest map contains an invalid entry")
            digest_map[relative] = digest
    envelope = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "forgecode.review",
        "workspace": ".",
        "workspace_identity": workspace_identity(guard),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report": report.to_dict(),
        "file_digests": dict(sorted(digest_map.items())),
    }
    # Bind metadata as well as the report.  Otherwise an attacker could edit
    # ``created_at`` or the digest map without changing report_sha256.
    envelope["artifact_sha256"] = _sha256(_canonical_json(envelope))
    encoded = _canonical_json(envelope)
    if len(encoded) > MAX_REVIEW_BYTES:
        raise ReviewArtifactError("review artifact exceeds size limit")
    try:
        assert_no_path_alias(destination.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        assert_no_path_alias(destination.parent)
    except (OSError, WorkspaceViolation) as exc:
        raise ReviewArtifactError("review artifact directory is unsafe") from exc
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        assert_no_path_alias(destination.parent)
        temporary.write_bytes(encoded)
        assert_no_path_alias(temporary)
        assert_no_path_alias(destination.parent)
        assert_no_path_alias(destination)
        os.replace(temporary, destination)
    except (OSError, WorkspaceViolation) as exc:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise ReviewArtifactError(f"could not write review artifact: {type(exc).__name__}") from exc
    return destination


def import_review(path: Path | str, guard: WorkspaceGuard, *, verify_files: bool = True) -> ReviewReport:
    """Load and verify an exported review artifact for this exact workspace."""
    try:
        source = guard.resolve(path, must_exist=True)
    except (OSError, ValueError, WorkspaceViolation) as exc:
        raise ReviewArtifactError("review artifact path is outside workspace") from exc
    if source.suffix.lower() not in {".json", ".review"} or source == guard.root:
        raise ReviewArtifactError("review artifact must be a JSON file inside the workspace")
    try:
        assert_no_path_alias(source)
        before = source.stat()
        if before.st_size > MAX_REVIEW_BYTES:
            raise ReviewArtifactError("review artifact exceeds size limit")
        raw_bytes = source.read_bytes()
        after = source.stat()
        if (before.st_size, before.st_mtime_ns, getattr(before, "st_ino", 0)) != (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", 0)):
            raise ReviewArtifactError("review artifact changed while it was read")
        raw = bounded_json_loads(raw_bytes, parse_constant=_reject_nonfinite_json)
    except ReviewArtifactError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewArtifactError(f"review artifact is unreadable: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise ReviewArtifactError("review artifact must be an object")
    expected_keys = {"artifact_schema_version", "kind", "workspace", "workspace_identity", "created_at", "report", "file_digests", "artifact_sha256"}
    if set(raw) != expected_keys:
        raise ReviewArtifactError("review artifact envelope is invalid")
    supplied_artifact_digest = raw.get("artifact_sha256")
    unsigned_artifact = {key: value for key, value in raw.items() if key != "artifact_sha256"}
    if not isinstance(supplied_artifact_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", supplied_artifact_digest) or _sha256(_canonical_json(unsigned_artifact)) != supplied_artifact_digest:
        raise ReviewArtifactError("review artifact digest mismatch")
    if raw.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION or raw.get("kind") != "forgecode.review" or raw.get("workspace") != ".":
        raise ReviewArtifactError("unsupported review artifact")
    if raw.get("workspace_identity") != workspace_identity(guard):
        raise ReviewArtifactError("review artifact belongs to another workspace")
    if not isinstance(raw.get("file_digests"), dict) or len(raw["file_digests"]) > MAX_FILES:
        raise ReviewArtifactError("review artifact file digest map is invalid")
    report = _report_from_dict(raw.get("report"))
    if report.workspace_identity != workspace_identity(guard):
        raise ReviewArtifactError("review report belongs to another workspace")
    digest_map = raw["file_digests"]
    for relative, expected in digest_map.items():
        if not _safe_relative(relative) or (expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected))):
            raise ReviewArtifactError("review artifact contains an unsafe file digest entry")
        if verify_files:
            value = _current_file_bytes(guard, relative)
            current = _sha256(value) if value is not None else None
            if current != expected:
                raise ReviewArtifactError(f"review artifact is stale: {relative}")
    return report


__all__ = [
    "ARTIFACT_SCHEMA_VERSION", "MAX_REVIEW_BYTES", "REVIEW_SCHEMA_VERSION", "DiffHunk", "ReviewArtifactError", "ReviewBuilder", "ReviewError", "ReviewFinding", "ReviewReport", "SecurityCheckResult", "export_review", "import_review", "run_security_checks", "workspace_identity",
]
