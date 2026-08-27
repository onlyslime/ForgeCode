"""Append-only JSONL session storage for auditable agent runs."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..security.redaction import redact_value


def bounded(value: Any, *, max_string_chars: int = 20_000, max_items: int = 200) -> Any:
    if isinstance(value, dict):
        items = list(value.items())
        result = {str(key): bounded(item, max_string_chars=max_string_chars, max_items=max_items) for key, item in items[:max_items]}
        if len(items) > max_items:
            result["_truncated_items"] = len(items) - max_items
        return result
    if isinstance(value, (list, tuple)):
        result = [bounded(item, max_string_chars=max_string_chars, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            result.append({"_truncated_items": len(value) - max_items})
        return result
    if isinstance(value, str) and len(value) > max_string_chars:
        return value[:max_string_chars] + "\n[value truncated]"
    return value


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    return redact_value(value, secrets)


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    payload: dict[str, Any]
    timestamp: str

    @classmethod
    def create(cls, kind: str, payload: dict[str, Any]) -> "SessionEvent":
        return cls(kind=kind, payload=payload, timestamp=datetime.now(timezone.utc).isoformat())


class SessionStore:
    def __init__(self, path: Path, *, secrets: Iterable[str] = (), max_event_chars: int = 100_000):
        self.path = path
        self.secrets = tuple(secrets)
        # A valid event envelope (kind, payload and ISO timestamp) needs a
        # small amount of space even when the payload is empty.
        if max_event_chars < 128:
            raise ValueError("max_event_chars must be at least 128 characters")
        self.max_event_chars = max_event_chars

    def append(self, kind: str, payload: dict[str, Any]) -> SessionEvent:
        safe_payload = bounded(redact(payload, self.secrets))
        event = SessionEvent.create(kind, safe_payload)
        serialized = json.dumps(asdict(event), ensure_ascii=False, default=str)
        if len(serialized) > self.max_event_chars:
            # Bound the complete JSONL event, not only its payload. Timestamp
            # and envelope overhead varies slightly, so use a short binary
            # search to retain the largest safe preview.
            original = json.dumps(safe_payload, ensure_ascii=False, default=str)
            low, high = 0, len(original)
            best = {"truncated": True, "preview": ""}
            while low <= high:
                middle = (low + high) // 2
                candidate = {"truncated": True, "preview": original[:middle]}
                candidate_event = SessionEvent.create(kind, candidate)
                candidate_size = len(json.dumps(asdict(candidate_event), ensure_ascii=False, default=str))
                if candidate_size <= self.max_event_chars:
                    best = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            safe_payload = best
            event = SessionEvent.create(kind, safe_payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        return event

    def read(self) -> Iterator[SessionEvent]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    raw = json.loads(line)
                    yield SessionEvent(**raw)
