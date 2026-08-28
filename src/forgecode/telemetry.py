"""Privacy-preserving telemetry policy.

Only bounded event names and scalar metadata are accepted. ``off`` and
``offline`` never write or transmit anything; ``local`` writes an ignored
JSONL audit file for diagnostics.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Telemetry:
    MAX_RECORDS = 5_000
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
        safe = {"event": str(event)[:128], "timestamp": int(time.time())}
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[str(key)[:64]] = value
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
