"""Append-only JSONL session storage for auditable agent runs."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Iterator


_SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "token", "password", "secret", "cookie")


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    secret_values = tuple(secret for secret in secrets if secret)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item, secret_values)
        return result
    if isinstance(value, list):
        return [redact(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return [redact(item, secret_values) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secret_values:
            result = result.replace(secret, "[REDACTED]")
        return result.replace("Bearer ", "Bearer [REDACTED]")
    return value


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    payload: dict[str, Any]
    timestamp: str

    @classmethod
    def create(cls, kind: str, payload: dict[str, Any]) -> "SessionEvent":
        return cls(kind=kind, payload=payload, timestamp=datetime.now(timezone.utc).isoformat())


class SessionStore:
    def __init__(self, path: Path, *, secrets: Iterable[str] = ()):
        self.path = path
        self.secrets = tuple(secrets)

    def append(self, kind: str, payload: dict[str, Any]) -> SessionEvent:
        event = SessionEvent.create(kind, redact(payload, self.secrets))
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
