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
from .security.trust import TrustStore, TrustError


class ForgeCodeError(RuntimeError):
    """Typed embedding error carrying the original machine envelope."""
    def __init__(self, message: str, *, code: str = "request_failed", envelope: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.envelope = envelope


def invoke(argv: list[str], *, request_id: str | int | None = None, raise_for_status: bool = False, max_response_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Execute one bounded CLI request and return every JSONL envelope."""
    if not isinstance(argv, list) or len(argv) > 128 or any(not isinstance(item, str) or len(item) > 1_000 for item in argv):
        raise ValueError("argv must contain at most 128 bounded string arguments")
    if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes < 1:
        raise ValueError("max_response_bytes must be a positive integer")
    request: dict[str, Any] = {"argv": [*argv, "--jsonl"] if "--jsonl" not in argv and "--json" not in argv else list(argv)}
    if request_id is not None:
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            raise ValueError("request_id must be a string or integer")
        if isinstance(request_id, str) and (not request_id or len(request_id) > 256 or any(ch in request_id for ch in "\r\n")):
            raise ValueError("request_id must be non-empty, bounded, and newline-safe")
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


def session_result(session: str, *, workspace: str | None = None, request_id: str | int | None = None, raise_for_status: bool = False, max_response_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Retrieve a bounded background-session result through the RPC contract."""
    if not isinstance(session, str) or not session or len(session) > 512 or any(ch in session for ch in "\r\n"):
        raise ValueError("session must be bounded newline-safe text")
    params: dict[str, Any] = {"session": session}
    if workspace is not None:
        if not isinstance(workspace, str) or not workspace or len(workspace) > 1_000 or any(ch in workspace for ch in "\r\n"):
            raise ValueError("workspace must be bounded newline-safe text")
        params["workspace"] = workspace
    request = {"method": "session.result", "params": params}
    if request_id is not None: request["id"] = request_id
    return list(stream([request], raise_for_status=raise_for_status, max_response_bytes=max_response_bytes))


def stream(requests: Iterable[dict[str, Any]], *, raise_for_status: bool = False, max_items: int = 1024, max_response_bytes: int = 2_000_000) -> Iterable[dict[str, Any]]:
    """Process JSON-compatible RPC requests in order."""
    if isinstance(max_items, bool) or max_items < 1 or max_items > 100_000:
        raise ValueError("max_items must be between 1 and 100000")
    if isinstance(max_response_bytes, bool) or max_response_bytes < 1:
        raise ValueError("max_response_bytes must be positive")
    total = 0
    count = 0
    def encoded_requests() -> Iterable[str]:
        for item in requests:
            if not isinstance(item, dict):
                raise ValueError("stream requests must be objects")
            try:
                encoded = json.dumps(item, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("stream request must be JSON-serializable") from exc
            if len(encoded.encode("utf-8")) > 1_000_000:
                raise ValueError("stream request exceeds 1 MiB")
            yield encoded
    for line in serve_lines(encoded_requests()):
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


__all__ = ["ForgeCodeError", "invoke", "session_result", "stream"]


class EmbeddedSession:
    """Programmatic interactive session backed by the production CLI worker."""
    def __init__(self, workspace: str, *, mode: str = "plan", executable: str = "forgecode", max_events: int = 1024):
        if mode not in {"plan", "act"}:
            raise ValueError("mode must be plan or act")
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1 or max_events > 100_000:
            raise ValueError("max_events must be between 1 and 100000")
        command = [sys.executable, "-u", "-m", "forgecode"] if executable == "forgecode" else [executable]
        self._workspace, self._mode, self._command, self._environment, self._max_events = workspace, mode, command, dict(os.environ), max_events
        environment = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        self.process = subprocess.Popen([*command, "--workspace", workspace, "chat", "--mode", mode, "--jsonl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=environment)
        self._events: Queue[dict[str, Any]] = Queue(maxsize=max_events)
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
        if self._mode == "act":
            try:
                if not TrustStore(Path(self._workspace)).status().get("trusted", False):
                    raise ForgeCodeError("workspace trust is not granted", code="trust_required")
            except TrustError as exc:
                raise ForgeCodeError(str(exc), code="trust_required") from exc
        environment = dict(self._environment)
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        self.process = subprocess.Popen([*self._command, "--workspace", self._workspace, "chat", "--mode", self._mode, "--jsonl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=environment)
        self._events = Queue(maxsize=self._max_events)
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
        if self.process.stdin is None: raise ForgeCodeError("session stdin is closed", code="process_error")
        try:
            self.process.stdin.write(text.replace("\r", " ").replace("\n", " ") + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ForgeCodeError(str(exc)[:500] or "session process is unavailable", code="process_error") from exc

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
                try:
                    self.process.terminate()
                    self.process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        self.process.kill()
                    finally:
                        self.process.wait(timeout=2)
        for handle in (self.process.stdin, self.process.stdout, self.process.stderr):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass


__all__.append("EmbeddedSession")
