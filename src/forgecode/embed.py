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


class ForgeCodeError(RuntimeError):
    """Typed embedding error carrying the original machine envelope."""
    def __init__(self, message: str, *, code: str = "request_failed", envelope: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.envelope = envelope


def invoke(argv: list[str], *, request_id: str | int | None = None, raise_for_status: bool = False, max_response_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Execute one bounded CLI request and return every JSONL envelope."""
    request: dict[str, Any] = {"argv": [*argv, "--jsonl"] if "--jsonl" not in argv and "--json" not in argv else list(argv)}
    if request_id is not None:
        request["id"] = request_id
    lines = list(serve_lines([json.dumps(request)]))
    if sum(len(line.encode("utf-8")) for line in lines) > max_response_bytes:
        raise ForgeCodeError("response exceeds output limit", code="output_limit")
    try:
        envelopes = [json.loads(line) for line in lines]
    except ValueError as exc:
        raise ForgeCodeError("invalid JSON response", code="invalid_json") from exc
    if raise_for_status:
        for envelope in envelopes:
            if isinstance(envelope, dict) and envelope.get("ok") is False:
                error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
                raise ForgeCodeError(str(error.get("message", "request failed")), code=str(error.get("code", "request_failed")), envelope=envelope)
    return envelopes


def stream(requests: Iterable[dict[str, Any]], *, raise_for_status: bool = False, max_items: int = 1024, max_response_bytes: int = 2_000_000) -> Iterable[dict[str, Any]]:
    """Process JSON-compatible RPC requests in order."""
    if isinstance(max_items, bool) or max_items < 1 or max_items > 100_000:
        raise ValueError("max_items must be between 1 and 100000")
    if isinstance(max_response_bytes, bool) or max_response_bytes < 1:
        raise ValueError("max_response_bytes must be positive")
    total = 0
    count = 0
    for line in serve_lines(json.dumps(item, ensure_ascii=False) for item in requests):
        count += 1
        total += len(line.encode("utf-8"))
        if count > max_items or total > max_response_bytes:
            raise ForgeCodeError("response exceeds output limit", code="output_limit")
        try:
            envelope = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise ForgeCodeError("invalid JSON response", code="invalid_json") from exc
        if raise_for_status and isinstance(envelope, dict) and envelope.get("ok") is False:
            error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
            raise ForgeCodeError(str(error.get("message", "request failed")), code=str(error.get("code", "request_failed")), envelope=envelope)
        yield envelope


__all__ = ["ForgeCodeError", "invoke", "stream"]


class EmbeddedSession:
    """Programmatic interactive session backed by the production CLI worker."""
    def __init__(self, workspace: str, *, mode: str = "plan", executable: str = "forgecode"):
        if mode not in {"plan", "act"}:
            raise ValueError("mode must be plan or act")
        command = [sys.executable, "-u", "-m", "forgecode"] if executable == "forgecode" else [executable]
        self._workspace, self._mode, self._command, self._environment = workspace, mode, command, dict(os.environ)
        environment = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        self.process = subprocess.Popen([*command, "--workspace", workspace, "chat", "--mode", mode, "--jsonl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=environment)
        self._events: Queue[dict[str, Any]] = Queue()
        self._stderr: Queue[str] = Queue(maxsize=128)
        self._stderr_text = ""
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._err_reader.start()

    def reconnect(self) -> bool:
        """Restart a dead worker once, preserving workspace and mode binding."""
        if self.is_alive:
            return False
        environment = dict(self._environment)
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        self.process = subprocess.Popen([*self._command, "--workspace", self._workspace, "chat", "--mode", self._mode, "--jsonl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=environment)
        self._events = Queue()
        self._stderr_text = ""
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start(); self._err_reader.start()
        self._events.put({"kind": "process_reconnected", "workspace": self._workspace, "mode": self._mode})
        return True

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try: self._events.put(json.loads(line))
            except ValueError: continue
        self._events.put({"kind": "process_exit", "code": self.process.returncode})

    def _read_stderr(self) -> None:
        if self.process.stderr is None: return
        for line in self.process.stderr:
            self._stderr_text = (self._stderr_text + line)[-16_000:]

    @property
    def is_alive(self) -> bool:
        return self.process.poll() is None

    @property
    def returncode(self) -> int | None:
        return self.process.poll()

    @property
    def stderr(self) -> str:
        return self._stderr_text

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
