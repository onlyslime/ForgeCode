"""Privacy-preserving telemetry policy.

Only bounded event names and scalar metadata are accepted. ``off`` and
``offline`` never write or transmit anything; ``local`` writes an ignored
JSONL audit file for diagnostics.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


class Telemetry:
    MAX_RECORDS = 5_000
    SCHEMA_VERSION = 1
    _SENSITIVE_KEYS = ("prompt", "content", "secret", "token", "api_key", "password", "credential", "command", "args", "stdout", "stderr", "workspace", "environment", "env")
    def __init__(self, workspace: Path, *, mode: str = "off", offline: bool = False):
        self.workspace = Path(workspace).resolve()
        self.mode = "off" if offline else mode
        if self.mode not in {"off", "local", "on"}:
            raise ValueError("telemetry mode must be off, local, or on")

    @property
    def external_enabled(self) -> bool:
        return self.mode == "on"

    def record(self, event: str, **metadata: Any) -> bool:
        if self.mode != "local":
            return False
        event_text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(event))[:128] or "event"
        safe = {"schema_version": self.SCHEMA_VERSION, "event": event_text, "timestamp": int(time.time())}
        dropped = 0
        for key, value in metadata.items():
            key_text = str(key)[:64]
            if any(part in key_text.lower() for part in self._SENSITIVE_KEYS):
                dropped += 1
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                if isinstance(value, str) and len(value) > 256:
                    dropped += 1
                    continue
                safe[key_text] = value
            else:
                dropped += 1
        if dropped:
            safe["dropped_fields"] = dropped
        path = self.workspace / ".forgecode" / "telemetry.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(record)
        # Bound retention without exposing or rewriting records in off mode.
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > self.MAX_RECORDS:
                tmp = path.with_suffix(".trim.tmp")
                tmp.write_text("\n".join(lines[-self.MAX_RECORDS:]) + "\n", encoding="utf-8")
                tmp.replace(path)
        except OSError:
            pass
        return True


__all__ = ["Telemetry"]
