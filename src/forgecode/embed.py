"""Small in-process embedding API built on the public CLI/RPC envelope."""
from __future__ import annotations

import json
import subprocess
import threading
import sys
import os
from queue import Queue, Empty
from typing import Any, Iterable
from pathlib import Path

from .rpc import serve_lines


def invoke(argv: list[str], *, request_id: str | int | None = None) -> list[dict[str, Any]]:
    """Execute one bounded CLI request and return every JSONL envelope."""
    request: dict[str, Any] = {"argv": list(argv)}
    if request_id is not None:
        request["id"] = request_id
    return [json.loads(line) for line in serve_lines([json.dumps(request)])]


def stream(requests: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """Process JSON-compatible RPC requests in order."""
    for line in serve_lines(json.dumps(item, ensure_ascii=False) for item in requests):
        yield json.loads(line)


__all__ = ["invoke", "stream"]


class EmbeddedSession:
    """Programmatic interactive session backed by the production CLI worker."""
    def __init__(self, workspace: str, *, mode: str = "plan", executable: str = "forgecode"):
        if mode not in {"plan", "act"}:
            raise ValueError("mode must be plan or act")
        command = [sys.executable, "-u", "-m", "forgecode"] if executable == "forgecode" else [executable]
        environment = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        self.process = subprocess.Popen([*command, "--workspace", workspace, "chat", "--mode", mode, "--jsonl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=environment)
        self._events: Queue[dict[str, Any]] = Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try: self._events.put(json.loads(line))
            except ValueError: continue

    def send(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > 8_000: raise ValueError("message must be non-empty and bounded")
        if self.process.stdin is None: raise RuntimeError("session stdin is closed")
        self.process.stdin.write(text.replace("\r", " ").replace("\n", " ") + "\n"); self.process.stdin.flush()

    def cancel(self) -> None: self.send("/cancel")
    def pause(self) -> None: self.send("/pause")
    def resume(self) -> None: self.send("/resume")
    def poll(self, timeout: float = 0.0) -> dict[str, Any] | None:
        try: return self._events.get(timeout=max(0.0, timeout))
        except Empty: return None

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send("/quit")
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()


__all__.append("EmbeddedSession")
