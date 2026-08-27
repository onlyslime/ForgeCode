"""Append-only JSONL session storage for auditable agent runs."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    payload: dict[str, Any]
    timestamp: str

    @classmethod
    def create(cls, kind: str, payload: dict[str, Any]) -> "SessionEvent":
        return cls(kind=kind, payload=payload, timestamp=datetime.now(timezone.utc).isoformat())


class SessionStore:
    def __init__(self, path: Path):
        self.path = path

    def append(self, kind: str, payload: dict[str, Any]) -> SessionEvent:
        event = SessionEvent.create(kind, payload)
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
