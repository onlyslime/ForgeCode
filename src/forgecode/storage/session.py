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
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable, Iterator
import uuid

from ..security.redaction import redact_value
from ..security.json import bounded_json_loads as _decode_bounded_json
from ..security.workspace import WorkspaceViolation, assert_no_path_alias


MAX_SESSION_BYTES = 32_000_000
MAX_SESSION_EVENTS = 20_000


class SessionFormatError(ValueError):
    """A session line cannot be trusted as an event envelope."""


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _bounded_json_loads(value: str) -> Any:
    """Decode one bounded JSON value and normalize recursion failures."""
    try:
        return _decode_bounded_json(value, parse_constant=_reject_nonfinite_json)
    except ValueError as exc:
        raise SessionFormatError(str(exc)) from exc


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
        try:
            self.path = assert_no_path_alias(Path(path), message="session path is a symlink or junction alias")
        except WorkspaceViolation as exc:
            raise ValueError(str(exc)) from exc
        self.secrets = tuple(secret for secret in secrets if isinstance(secret, str) and secret)
        if max_event_chars < 128:
            raise ValueError("max_event_chars must be at least 128 characters")
        self.max_event_chars = max_event_chars
        # Retain the caller-supplied identity separately from the identity
        # discovered in an existing stream. Inspection must diagnose a
        # requested run that does not match the persisted stream, rather than
        # presenting that stream as safe to continue.
        self._requested_run_id = run_id
        if run_id is not None and not _safe_run_id(run_id):
            raise ValueError("run_id must contain only bounded letters, digits, '-' or '_'")
        discovered = self._discover_run_id()
        if discovered is not None and not _safe_run_id(discovered):
            discovered = None
        self.run_id = run_id or discovered or uuid.uuid4().hex
        self.mode = mode
        self._lock = threading.RLock()
        self._next_sequence = self._discover_next_sequence()
        # Full-stream validation is deliberately performed before appending
        # to an existing log, but rereading the complete stream for every
        # event makes a short run unnecessarily expensive (especially on
        # Windows, where each read also walks reparse-point metadata).  Keep
        # the metadata observed after the last validated append.  The
        # interprocess lock serializes writers; a size/mtime/ctime/inode
        # change from another writer invalidates this cache and forces the
        # complete consistency scan again.  This is an optimization only:
        # read/inspection paths always perform their full validation.
        self._validated_signature: tuple[int, int, int, int, int] | None = None
        self.last_read_issues: tuple[SessionReadIssue, ...] = ()

    def _file_signature(self) -> tuple[int, int, int, int, int] | None:
        """Return a bounded identity for the session stream, or ``None``.

        ``st_ino``/``st_dev`` distinguish replacement files while size and
        nanosecond timestamps detect ordinary external appends/edits.  A
        failed stat is treated as changed so the caller takes the conservative
        validation path.
        """
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (
            int(getattr(stat, "st_dev", 0)),
            int(getattr(stat, "st_ino", 0)),
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", 0)),
            int(getattr(stat, "st_ctime_ns", 0)),
        )

    def _discover_next_sequence(self) -> int:
        if not self.path.is_file():
            return 1
        highest = 0
        legacy_count = 0
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        raw = _bounded_json_loads(line)
                        sequence = raw.get("sequence", 0) if isinstance(raw, dict) else 0
                        if isinstance(raw, dict) and "sequence" not in raw:
                            legacy_count += 1
                        if isinstance(sequence, int) and not isinstance(sequence, bool):
                            highest = max(highest, sequence)
                    except (json.JSONDecodeError, ValueError, OSError):
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
                    raw = _bounded_json_loads(line)
                    value = raw.get("run_id") if isinstance(raw, dict) else None
                    if isinstance(value, str) and value:
                        return value
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
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
        with self._lock, self._interprocess_lock():
            # Validate the lexical path before any metadata lookup.  In
            # particular, do not let the signature stat follow a symlink or
            # junction that was swapped in by an untrusted writer.
            try:
                assert_no_path_alias(self.path, message="session path is a symlink or junction alias")
            except WorkspaceViolation as exc:
                raise SessionFormatError(str(exc)) from exc
            # Another process may have appended since this store was created;
            # recalculate the sequence while holding the OS-level lock so two
            # writers cannot emit duplicate sequence numbers.
            current_signature = self._file_signature()
            # A failed stat is represented by ``None`` and must be treated as
            # changed (``None == None`` would otherwise accidentally bypass
            # the conservative validation path for a newly created or
            # temporarily unavailable stream).
            stream_changed = current_signature is None or current_signature != self._validated_signature
            if stream_changed:
                self._next_sequence = max(self._next_sequence, self._discover_next_sequence())
            if stream_changed:
                self._validate_append_target()
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
                # The configured bound also applies to the envelope itself.
                # Very small limits (the constructor intentionally accepts
                # values down to 128 for compatibility) cannot represent a
                # valid v1 event, even after payload truncation.  Refuse the
                # append instead of writing a line that every subsequent read
                # would classify as corrupt/oversized.
                if len(serialized) > self.max_event_chars:
                    raise ValueError("max_event_chars is too small for the event envelope")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._next_sequence += 1
            # Capture the post-write identity while still holding the
            # interprocess lock.  A following append by this store can skip a
            # redundant full scan; another process changes the signature and
            # is validated conservatively on its next append.
            self._validated_signature = self._file_signature()
            return event

    @contextmanager
    def _interprocess_lock(self):
        """Serialize appenders across processes without third-party deps.

        POSIX uses ``flock`` and Windows uses ``msvcrt.locking`` on a sibling
        lock file.  The lock file is deliberately retained (and lives under
        ignored runtime storage in normal operation) so a crash cannot create
        a missing-file race; the OS releases the advisory lock automatically.
        """
        lock_path = Path(str(self.path) + ".lock")
        try:
            assert_no_path_alias(lock_path, message="session lock path is a symlink or junction alias")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+b")
        except (OSError, WorkspaceViolation) as exc:
            raise SessionFormatError(f"cannot open session lock: {type(exc).__name__}") from exc
        acquired = False
        try:
            deadline = time.monotonic() + 10.0
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                while not acquired:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise SessionFormatError("timed out waiting for session lock")
                        time.sleep(0.01)
            else:
                import fcntl

                while not acquired:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise SessionFormatError("timed out waiting for session lock")
                        time.sleep(0.01)
            yield
        finally:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()

    def _validate_append_target(self) -> None:
        """Refuse to append to a stream that cannot be safely continued.

        The append-only log is also the audit boundary.  Silently adding a
        fresh run id after a corrupt, legacy, gapped, or mixed stream would
        make reconstruction ambiguous and could turn untrusted history into
        apparent authorization.  Callers that need to inspect such a file
        can still use ``read_with_issues``; continuation requires an explicit
        resume/fork workflow that creates a new stream.
        """
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return
        result = self.read_with_issues()
        if result.issues:
            first = result.issues[0]
            raise SessionFormatError(f"cannot append to an inconsistent session (line {first.line}: {first.message})")
        if any(event.schema_version == 0 for event in result.events):
            raise SessionFormatError("cannot append to a legacy session; create a fork")
        run_ids = {event.run_id for event in result.events if event.run_id}
        if len(run_ids) > 1 or (run_ids and self.run_id not in run_ids):
            raise SessionFormatError("cannot append with a different or mixed run id")

    def read_with_issues(self, *, strict: bool = False) -> SessionReadResult:
        # A read is an observation boundary.  Even when the stream's
        # metadata appears unchanged, a caller may have discovered an
        # in-place corruption or may be racing an external writer.  Force the
        # next append to perform the conservative full validation; a clean
        # append will establish a new cache signature after its atomic write.
        self._validated_signature = None
        events: list[SessionEvent] = []
        event_lines: list[int] = []
        issues: list[SessionReadIssue] = []
        try:
            assert_no_path_alias(self.path, message="session path is a symlink or junction alias")
        except WorkspaceViolation as exc:
            issue = SessionReadIssue(0, str(exc))
            self.last_read_issues = (issue,)
            if strict:
                raise SessionFormatError(issue.message) from exc
            return SessionReadResult((), (issue,))
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
            before_stat = self.path.stat()
            if before_stat.st_size > MAX_SESSION_BYTES:
                issue = SessionReadIssue(0, f"session exceeds the {MAX_SESSION_BYTES}-byte safety limit")
                self.last_read_issues = (issue,)
                if strict:
                    raise SessionFormatError(issue.message)
                return SessionReadResult((), (issue,))
            raw_bytes = self.path.read_bytes()
            after_stat = self.path.stat()
            # Revalidate the directory entry after the bytes are captured.
            # An alias swap during a review/inspection must become a
            # structured issue, never a trusted late stream.
            assert_no_path_alias(self.path, message="session path is a symlink or junction alias")
        except (OSError, UnicodeError, WorkspaceViolation) as exc:
            issue = SessionReadIssue(0, f"cannot read session: {type(exc).__name__}: {exc}")
            self.last_read_issues = (issue,)
            if strict:
                raise SessionFormatError(issue.message) from exc
            return SessionReadResult((), (issue,))
        if len(raw_bytes) > MAX_SESSION_BYTES:
            issue = SessionReadIssue(0, f"session exceeds the {MAX_SESSION_BYTES}-byte safety limit")
            self.last_read_issues = (issue,)
            if strict:
                raise SessionFormatError(issue.message)
            return SessionReadResult((), (issue,))
        if (before_stat.st_size, before_stat.st_mtime_ns) != (after_stat.st_size, after_stat.st_mtime_ns):
            issues.append(SessionReadIssue(0, "session changed while it was read"))
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            issue = SessionReadIssue(0, f"UnicodeDecodeError: session is not valid UTF-8 at byte {exc.start}")
            issues.append(issue)
            self.last_read_issues = tuple(issues)
            if strict:
                raise SessionFormatError(issue.message) from exc
            return SessionReadResult((), tuple(issues))
        for line_number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                if len(line) > self.max_event_chars:
                    issue = SessionReadIssue(line_number, f"event line exceeds the {self.max_event_chars}-character safety limit")
                    issues.append(issue)
                    if strict:
                        self.last_read_issues = tuple(issues)
                        raise SessionFormatError(f"line {line_number}: {issue.message}")
                    continue
                if len(events) >= MAX_SESSION_EVENTS:
                    issue = SessionReadIssue(line_number, f"session exceeds the {MAX_SESSION_EVENTS}-event safety limit")
                    issues.append(issue)
                    if strict:
                        self.last_read_issues = tuple(issues)
                        raise SessionFormatError(f"line {line_number}: {issue.message}")
                    break
                try:
                    raw = _bounded_json_loads(line)
                    if not isinstance(raw, dict):
                        raise SessionFormatError("event must be an object")
                    if "schema_version" not in raw:
                        # v0.0.4 envelope: preserve it as legacy schema 0 so
                        # inspection can distinguish it from durable events.
                        raw = {**raw, "schema_version": 0}
                    event = SessionEvent(**raw)
                    if not isinstance(event.kind, str) or not event.kind.strip() or len(event.kind) > 128 or not isinstance(event.payload, dict) or not isinstance(event.timestamp, str):
                        raise SessionFormatError("event has invalid envelope types")
                    if len(event.timestamp) > 128:
                        raise SessionFormatError("event timestamp is too long")
                    try:
                        parsed_timestamp = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise SessionFormatError("event timestamp is not valid ISO-8601") from exc
                    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
                        raise SessionFormatError("event timestamp must include a timezone")
                    if event.schema_version >= 1 and not _safe_run_id(event.run_id):
                        raise SessionFormatError("event run_id must be bounded text")
                    if event.mode is not None and not isinstance(event.mode, str):
                        raise SessionFormatError("event mode must be text or null")
                    if isinstance(event.schema_version, bool) or not isinstance(event.schema_version, int) or event.schema_version not in {0, 1}:
                        raise SessionFormatError(f"unsupported schema_version {event.schema_version!r}")
                    if event.schema_version >= 1 and (not isinstance(event.sequence, int) or isinstance(event.sequence, bool) or event.sequence < 1):
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

        # Validate explicit lifecycle evidence at read time. Status, export
        # and recovery must not treat a forged transition after a terminal
        # state as authorization to continue. Older hand-written events that
        # only carry ``to`` remain inspectable for compatibility.
        lifecycle_state: str | None = None
        for line, event in zip(event_lines, events):
            if event.kind == "run_created":
                # An interactive session may contain several bounded AgentLoop
                # turns under one run id. Each loop emits run_created and
                # starts a fresh lifecycle; this marker is the explicit reset
                # boundary, not an implicit terminal-state transition.
                lifecycle_state = "created"
                continue
            if event.kind != "state_transition" or not isinstance(event.payload, dict):
                continue
            source = event.payload.get("from")
            target = event.payload.get("to")
            if source is None and target is None:
                continue
            if not isinstance(source, str) or not isinstance(target, str):
                issues.append(SessionReadIssue(line, "state transition must contain text from/to states"))
                continue
            if source not in _STATE_TRANSITIONS or target not in _STATE_TRANSITIONS:
                issues.append(SessionReadIssue(line, "state transition contains an unknown run state"))
                continue
            if lifecycle_state is not None and source != lifecycle_state:
                issues.append(SessionReadIssue(line, f"state transition source {source!r} does not follow {lifecycle_state!r}"))
                continue
            if target not in _STATE_TRANSITIONS[source]:
                issues.append(SessionReadIssue(line, f"invalid terminal or lifecycle transition: {source} -> {target}"))
                continue
            lifecycle_state = target

        # Append rejects these conditions, but inspection, status, export and
        # recovery must expose them too. Otherwise a mixed stream could look
        # like a valid partial audit until a write is attempted.
        v1_events = [(line, event) for line, event in zip(event_lines, events) if event.schema_version >= 1]
        legacy_events = [(line, event) for line, event in zip(event_lines, events) if event.schema_version == 0]
        if v1_events and legacy_events:
            first_mixed_line = min(v1_events[0][0], legacy_events[0][0])
            issues.append(SessionReadIssue(first_mixed_line, "legacy and v1 events are mixed; session is inspect-only"))

        run_id_values = {event.run_id for _, event in v1_events + legacy_events if event.run_id}
        if len(run_id_values) > 1:
            first_run_id = next(iter(run_id_values))
            differing_line = next((line for line, event in zip(event_lines, events) if event.run_id and event.run_id != first_run_id), event_lines[0] if event_lines else 0)
            issues.append(SessionReadIssue(differing_line, "session contains mixed run_id values; session is inspect-only"))
        if self._requested_run_id is not None and run_id_values and self._requested_run_id not in run_id_values:
            issues.append(SessionReadIssue(event_lines[0] if event_lines else 0, "session run_id differs from requested store run_id (different or mixed run id); session is inspect-only"))

        if strict and issues:
            first = issues[0]
            self.last_read_issues = tuple(issues)
            raise SessionFormatError(f"line {first.line}: {first.message}")
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
            safe_event = asdict(event)
            safe_event["payload"] = bounded(redact_value(safe_event.get("payload", {}), self.secrets))
            line = json.dumps(safe_event, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
            if used + len(line) > limit:
                break
            chunks.append(line)
            used += len(line)
        return "".join(chunks)


def _safe_run_id(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and bool(re.fullmatch(r"[A-Za-z0-9_-]+", value))


_STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"discovering", "cancelled", "failed"}),
    "discovering": frozenset({"planning", "paused", "failed", "cancelled", "recovery_required"}),
    "planning": frozenset({"awaiting_approval", "verifying", "completed", "failed", "cancelled", "paused"}),
    "awaiting_approval": frozenset({"acting", "paused", "cancelled", "failed", "recovery_required"}),
    "acting": frozenset({"discovering", "verifying", "completed", "paused", "failed", "cancelled", "recovery_required"}),
    "verifying": frozenset({"completed", "acting", "awaiting_approval", "discovering", "paused", "failed", "cancelled", "recovery_required"}),
    "paused": frozenset({"discovering", "cancelled", "failed", "recovery_required"}),
    "recovery_required": frozenset({"discovering", "cancelled", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


__all__ = ["SessionEvent", "SessionFormatError", "SessionReadIssue", "SessionReadResult", "SessionStore", "bounded", "redact"]
