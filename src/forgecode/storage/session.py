"""Durable, bounded and redacted JSONL event storage.

The v0.0.4 API (``append(kind, payload)`` and ``SessionEvent.kind``) remains
valid. New events carry a versioned envelope and monotonic sequence so a
session can be inspected without treating its contents as executable input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Iterable, Iterator
import uuid

from ..security.redaction import redact_value


class SessionFormatError(ValueError):
    """A session line cannot be trusted as an event envelope."""


@dataclass(frozen=True)
class SessionReadIssue:
    line: int
    message: str


@dataclass(frozen=True)
class SessionReadResult:
    events: tuple["SessionEvent", ...]
    issues: tuple[SessionReadIssue, ...]


def _safe_json_value(value: Any, *, max_string_chars: int = 20_000, max_items: int = 200, _seen: set[int] | None = None, _depth: int = 0) -> Any:
    """Normalize values before redaction/JSON encoding, including odd inputs."""
    if _seen is None:
        _seen = set()
    if _depth > 20:
        return "[maximum nesting depth exceeded]"
    if isinstance(value, (str, int, bool)) or value is None:
        if isinstance(value, str) and len(value) > max_string_chars:
            return value[:max_string_chars] + "\n[value truncated]"
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else f"[{value!r} omitted]"
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, bytes):
        return f"[bytes omitted: {len(value)} bytes]"
    object_id = id(value)
    if object_id in _seen:
        return "[circular reference omitted]"
    _seen.add(object_id)
    try:
        if isinstance(value, dict):
            items = sorted(value.items(), key=lambda item: str(item[0]))
            result = {
                str(key): _safe_json_value(item, max_string_chars=max_string_chars, max_items=max_items, _seen=_seen, _depth=_depth + 1)
                for key, item in items[:max_items]
            }
            if len(items) > max_items:
                result["_truncated_items"] = len(items) - max_items
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            values = list(value)
            if isinstance(value, (set, frozenset)):
                values.sort(key=repr)
            result = [_safe_json_value(item, max_string_chars=max_string_chars, max_items=max_items, _seen=_seen, _depth=_depth + 1) for item in values[:max_items]]
            if len(values) > max_items:
                result.append({"_truncated_items": len(values) - max_items})
            return result
        return str(value)[:max_string_chars]
    finally:
        _seen.discard(object_id)


def bounded(value: Any, *, max_string_chars: int = 20_000, max_items: int = 200) -> Any:
    """Public compatibility wrapper for bounded event payloads."""
    return _safe_json_value(value, max_string_chars=max_string_chars, max_items=max_items)


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    return redact_value(value, secrets)


@dataclass(frozen=True)
class SessionEvent:
    # Keep the original three fields first for source compatibility.
    kind: str
    payload: dict[str, Any]
    timestamp: str
    schema_version: int = 1
    run_id: str = ""
    sequence: int = 0
    mode: str | None = None
    operation_id: str | None = None
    outcome: str | None = None
    error_code: str | None = None

    @classmethod
    def create(
        cls,
        kind: str,
        payload: dict[str, Any],
        *,
        schema_version: int = 1,
        run_id: str = "",
        sequence: int = 0,
        mode: str | None = None,
        operation_id: str | None = None,
        outcome: str | None = None,
        error_code: str | None = None,
    ) -> "SessionEvent":
        return cls(
            kind=kind,
            payload=payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
            schema_version=schema_version,
            run_id=run_id,
            sequence=sequence,
            mode=mode,
            operation_id=operation_id,
            outcome=outcome,
            error_code=error_code,
        )


class SessionStore:
    """Append-only JSONL store with bounded, recoverable reads."""

    def __init__(self, path: Path, *, secrets: Iterable[str] = (), max_event_chars: int = 100_000, run_id: str | None = None, mode: str | None = None):
        self.path = Path(path)
        self.secrets = tuple(secret for secret in secrets if isinstance(secret, str) and secret)
        if max_event_chars < 128:
            raise ValueError("max_event_chars must be at least 128 characters")
        self.max_event_chars = max_event_chars
        self.run_id = run_id or self._discover_run_id() or uuid.uuid4().hex
        self.mode = mode
        self._lock = threading.RLock()
        self._next_sequence = self._discover_next_sequence()
        self.last_read_issues: tuple[SessionReadIssue, ...] = ()

    def _discover_next_sequence(self) -> int:
        if not self.path.is_file():
            return 1
        highest = 0
        legacy_count = 0
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        raw = json.loads(line)
                        sequence = raw.get("sequence", 0) if isinstance(raw, dict) else 0
                        if isinstance(raw, dict) and "sequence" not in raw:
                            legacy_count += 1
                        if isinstance(sequence, int) and not isinstance(sequence, bool):
                            highest = max(highest, sequence)
                    except (json.JSONDecodeError, OSError):
                        continue
        except (OSError, UnicodeError):
            return 1
        # Legacy v0.0.4 records have no sequence. Reserve their line count so
        # an appended v1 event cannot reuse sequence 1.
        return max(highest, legacy_count) + 1

    def _discover_run_id(self) -> str | None:
        if not self.path.is_file():
            return None
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    raw = json.loads(line)
                    value = raw.get("run_id") if isinstance(raw, dict) else None
                    if isinstance(value, str) and value:
                        return value
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return None

    @property
    def last_sequence(self) -> int:
        """Last sequence successfully persisted in this process/file."""
        return max(0, self._next_sequence - 1)

    def append(self, kind: str, payload: dict[str, Any], *, mode: str | None = None, operation_id: str | None = None, outcome: str | None = None, error_code: str | None = None) -> SessionEvent:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("event kind must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("event payload must be an object")
        with self._lock:
            # Normalize first so cyclic containers and non-JSON values cannot
            # recurse through the redaction walker or reach json.dumps.
            safe_payload = bounded(redact_value(bounded(payload), self.secrets))
            sequence = self._next_sequence
            selected_mode = mode or self.mode
            event = SessionEvent.create(kind.strip(), safe_payload, run_id=self.run_id, sequence=sequence, mode=selected_mode, operation_id=operation_id, outcome=outcome, error_code=error_code)
            serialized = json.dumps(asdict(event), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(serialized) > self.max_event_chars:
                original = json.dumps(safe_payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                low, high = 0, len(original)
                best_payload: dict[str, Any] = {"truncated": True, "preview": ""}
                while low <= high:
                    middle = (low + high) // 2
                    candidate = {"truncated": True, "preview": original[:middle]}
                    candidate_event = SessionEvent.create(kind.strip(), candidate, run_id=self.run_id, sequence=sequence, mode=selected_mode, operation_id=operation_id, outcome=outcome, error_code=error_code)
                    candidate_size = len(json.dumps(asdict(candidate_event), ensure_ascii=False, allow_nan=False, separators=(",", ":")))
                    if candidate_size <= self.max_event_chars:
                        best_payload = candidate
                        low = middle + 1
                    else:
                        high = middle - 1
                event = SessionEvent.create(kind.strip(), best_payload, run_id=self.run_id, sequence=sequence, mode=selected_mode, operation_id=operation_id, outcome=outcome, error_code=error_code)
                serialized = json.dumps(asdict(event), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._next_sequence += 1
            return event

    def read_with_issues(self, *, strict: bool = False) -> SessionReadResult:
        events: list[SessionEvent] = []
        event_lines: list[int] = []
        issues: list[SessionReadIssue] = []
        if not self.path.exists():
            self.last_read_issues = ()
            return SessionReadResult((), ())
        if not self.path.is_file():
            issue = SessionReadIssue(0, "session path is not a file")
            self.last_read_issues = (issue,)
            if strict:
                raise SessionFormatError(issue.message)
            return SessionReadResult((), (issue,))
        try:
            stream = self.path.open(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            issue = SessionReadIssue(0, f"cannot open session: {type(exc).__name__}: {exc}")
            self.last_read_issues = (issue,)
            if strict:
                raise SessionFormatError(issue.message) from exc
            return SessionReadResult((), (issue,))
        with stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        raise SessionFormatError("event must be an object")
                    if "schema_version" not in raw:
                        # v0.0.4 envelope: preserve it as legacy schema 0 so
                        # inspection can distinguish it from durable events.
                        raw = {**raw, "schema_version": 0}
                    event = SessionEvent(**raw)
                    if not isinstance(event.kind, str) or not isinstance(event.payload, dict) or not isinstance(event.timestamp, str):
                        raise SessionFormatError("event has invalid envelope types")
                    if isinstance(event.schema_version, bool) or not isinstance(event.schema_version, int) or event.schema_version not in {0, 1}:
                        raise SessionFormatError(f"unsupported schema_version {event.schema_version!r}")
                    if event.sequence and (not isinstance(event.sequence, int) or isinstance(event.sequence, bool) or event.sequence < 1):
                        raise SessionFormatError("event sequence must be a positive integer")
                    events.append(event)
                    event_lines.append(line_number)
                except (json.JSONDecodeError, TypeError, ValueError, SessionFormatError) as exc:
                    issue = SessionReadIssue(line_number, f"{type(exc).__name__}: {exc}")
                    issues.append(issue)
                    if strict:
                        self.last_read_issues = tuple(issues)
                        raise SessionFormatError(f"line {line_number}: {issue.message}") from exc
        previous = 0
        for index, event in enumerate(events):
            if event.schema_version >= 1 and event.sequence:
                if event.sequence <= previous:
                    issues.append(SessionReadIssue(event_lines[index], "event sequence is not strictly increasing"))
                elif previous and event.sequence != previous + 1:
                    issues.append(SessionReadIssue(event_lines[index], f"event sequence gap after {previous}"))
                previous = event.sequence
        self.last_read_issues = tuple(issues)
        return SessionReadResult(tuple(events), tuple(issues))

    def read(self, *, strict: bool = False) -> Iterator[SessionEvent]:
        result = self.read_with_issues(strict=strict)
        yield from result.events

    def export(self, *, max_chars: int | None = None) -> str:
        limit = self.max_event_chars * 20 if max_chars is None else max_chars
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 128:
            raise ValueError("export max_chars must be an integer >= 128")
        chunks: list[str] = []
        used = 0
        for event in self.read():
            line = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n"
            if used + len(line) > limit:
                break
            chunks.append(line)
            used += len(line)
        return "".join(chunks)


__all__ = ["SessionEvent", "SessionFormatError", "SessionReadIssue", "SessionReadResult", "SessionStore", "bounded", "redact"]
