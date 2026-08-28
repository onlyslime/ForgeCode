"""Line-delimited RPC bridge for programmatic clients.

Each request is a JSON object containing ``argv`` (CLI arguments excluding the
program name). The response is the same single ForgeCode JSON envelope emitted
by the CLI, preserving command semantics and exit codes.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import threading
import uuid
import time
from pathlib import Path
from typing import Any, Iterable

from .application.commands import main
from .security.trust import TrustStore, TrustError

_SESSION_LOCK = threading.RLock()
_RPC_SESSIONS: dict[str, dict[str, Any]] = {}
_RPC_REPLAYS: dict[str | int, tuple[str, ...]] = {}
_RPC_FINGERPRINTS: dict[str | int, str] = {}
_SESSION_TTL_SECONDS = 8 * 60 * 60
_MAX_RPC_SESSIONS = 256
_MAX_SESSION_EVENTS = 512
_MAX_REQUEST_LINE_BYTES = 1_048_576


def _session_record_path(info: dict[str, Any], handle: str) -> Path:
    return Path(info["workspace"]) / ".forgecode" / "rpc-sessions" / f"{handle}.json"


def _persist_session(handle: str, info: dict[str, Any]) -> None:
    path = _session_record_path(info, handle)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: info.get(key) for key in ("workspace", "mode", "session_path", "state", "sequence", "created_at", "cancel_requested")}
    payload["events"] = list(info.get("events", []))[-_MAX_SESSION_EVENTS:]
    tmp = path.with_suffix(".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except OSError:
            # Some Windows/network filesystems do not expose file fsync;
            # flush plus atomic replace still preserves a complete record.
            pass
    tmp.replace(path)
    try:
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    except OSError:
        pass


def _remove_session_record(handle: str, info: dict[str, Any]) -> None:
    try:
        _session_record_path(info, handle).unlink(missing_ok=True)
    except OSError:
        # A failed revocation must remain visible to the caller; do not hide it.
        raise ValueError("could not revoke persisted session handle")


def _load_session(handle: str, workspace_hint: str | None = None) -> dict[str, Any] | None:
    if not isinstance(handle, str) or len(handle) != 32 or any(ch not in "0123456789abcdef" for ch in handle):
        return None
    roots = [Path(workspace_hint).expanduser().resolve()] if workspace_hint else [Path.cwd()]
    for root in roots:
        candidate_dir = root / ".forgecode" / "rpc-sessions"
        candidate = candidate_dir / f"{handle}.json"
        if not candidate.is_file():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            workspace = Path(str(raw["workspace"])).expanduser().resolve()
            if not workspace.is_dir() or (workspace / ".forgecode" / "rpc-sessions" / f"{handle}.json").resolve() != candidate.resolve():
                continue
            events = raw.get("events", [])
            if not isinstance(events, list): events = []
            info = {"workspace": str(workspace), "mode": raw["mode"], "session_path": raw["session_path"], "state": raw.get("state", "idle"), "sequence": int(raw.get("sequence", 0)), "events": events[-_MAX_SESSION_EVENTS:], "created_monotonic": time.monotonic(), "created_at": raw.get("created_at"), "cancel_requested": bool(raw.get("cancel_requested", False))}
            if info["mode"] not in {"plan", "act"}: continue
            created_at = info.get("created_at")
            if isinstance(created_at, (int, float)) and time.time() - float(created_at) > _SESSION_TTL_SECONDS:
                continue
            return info
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _prune_sessions() -> None:
    now = time.monotonic()
    stale = [key for key, value in _RPC_SESSIONS.items() if now - float(value.get("created_monotonic", now)) > _SESSION_TTL_SECONDS]
    for key in stale: _RPC_SESSIONS.pop(key, None)
    while len(_RPC_SESSIONS) >= _MAX_RPC_SESSIONS:
        oldest = min(_RPC_SESSIONS, key=lambda key: float(_RPC_SESSIONS[key].get("created_monotonic", now)))
        _RPC_SESSIONS.pop(oldest, None)


def serve_lines(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        method = None
        request_id = None
        try:
            if not isinstance(line, str) or len(line.encode("utf-8", errors="replace")) > _MAX_REQUEST_LINE_BYTES:
                raise ValueError("request_too_large: JSONL request exceeds 1 MiB")
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get("argv", []), list):
                raise ValueError("request must be an object with argv array")
            request_id = request.get("id")
            if request_id is not None and (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)):
                raise ValueError("request id must be a string or integer")
            if isinstance(request_id, str) and (not request_id or len(request_id) > 256 or any(ch in request_id for ch in "\r\n")):
                raise ValueError("request id must be non-empty, bounded, and newline-safe")
            if request_id is not None:
                fingerprint = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                with _SESSION_LOCK:
                    replay = _RPC_REPLAYS.get(request_id)
                    previous_fingerprint = _RPC_FINGERPRINTS.get(request_id)
                if previous_fingerprint is not None and previous_fingerprint != fingerprint:
                    raise ValueError("request id was already used for a different request")
                if replay is not None:
                    yield from replay
                    continue
            argv_value = request.get("argv", [])
            method = request.get("method")
            handle = None
            if method is not None:
                if not isinstance(method, str) or len(method) > 128 or any(ch.isspace() for ch in method):
                    raise ValueError("method must be bounded non-whitespace text")
                method_map = {"trust.status": ["trust", "status"], "trust.grant": ["trust", "grant"], "trust.revoke": ["trust", "revoke"], "provider.list": ["provider", "list"], "provider.health": ["provider", "health"], "config.show": ["config", "show"], "doctor": ["doctor"], "login": ["login"], "run": ["run"], "session.open": ["session", "open"], "session.run": ["run"], "session.events": ["session", "events"], "session.cancel": ["session", "cancel"], "session.pause": ["session", "pause"], "session.resume": ["session", "resume"], "session.approval": ["session", "approval"], "session.close": ["session", "close"], "session.status": ["session", "status"], "session.inspect": ["session", "inspect"], "session.tree": ["session", "tree"], "session.export": ["session", "export"]}
                if method not in method_map:
                    raise ValueError("unsupported RPC method")
                if argv_value:
                    raise ValueError("method and argv cannot be combined")
                params = request.get("params", {})
                if params is None: params = {}
                if not isinstance(params, dict) or len(params) > 16:
                    raise ValueError("params must be a bounded object")
                argv_value = list(method_map[method])
                if method.startswith("trust.") and params.get("workspace") is not None:
                    workspace = params.get("workspace")
                    if not isinstance(workspace, str) or len(workspace) > 1_000 or any(ch in workspace for ch in "\r\n"):
                        raise ValueError("trust.workspace is invalid")
                    argv_value = ["--workspace", workspace, *argv_value]
                if method == "session.open":
                    workspace = params.get("workspace", ".")
                    mode = params.get("mode", "plan")
                    if not isinstance(workspace, str) or len(workspace) > 1_000 or any(ch in workspace for ch in "\r\n"):
                        raise ValueError("session.open.workspace is invalid")
                    workspace = str(Path(workspace).expanduser().resolve())
                    if not Path(workspace).is_dir():
                        raise ValueError("session.open.workspace must be an existing directory")
                    if mode not in {"plan", "act"}:
                        raise ValueError("session.open.mode must be plan or act")
                    requested_handle = params.get("session")
                    if requested_handle is not None:
                        if not isinstance(requested_handle, str) or len(requested_handle) != 32:
                            raise ValueError("session.open.session handle is invalid")
                        restored = _load_session(requested_handle, workspace)
                        if restored is None: raise ValueError("session handle is not recoverable")
                        if restored["mode"] == "act":
                            try:
                                if not TrustStore(Path(restored["workspace"])).status().get("trusted", False):
                                    raise ValueError("workspace trust is not granted")
                            except TrustError:
                                raise ValueError("workspace trust is not granted")
                        handle = requested_handle
                        with _SESSION_LOCK: _RPC_SESSIONS[handle] = restored
                        payload = {"schema_version": 1, "kind": "session", "ok": True, "command": "session.open", "data": {"session": handle, "workspace": restored["workspace"], "mode": restored["mode"], "state": restored.get("state"), "sequence": restored.get("sequence", 0), "cancel_requested": bool(restored.get("cancel_requested", False)), "recovered": True}, "exit_code": 0}
                        if request_id is not None: payload["id"] = request_id
                        yield json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        continue
                    handle = uuid.uuid4().hex
                    with _SESSION_LOCK:
                        _prune_sessions()
                        _RPC_SESSIONS[handle] = {"workspace": workspace, "mode": mode, "session_path": f".forgecode/sessions/{handle}.jsonl", "state": "idle", "sequence": 0, "events": [], "created_monotonic": time.monotonic(), "created_at": int(time.time()), "cancel_requested": False}
                        _persist_session(handle, _RPC_SESSIONS[handle])
                    payload = {"schema_version": 1, "kind": "session", "ok": True, "command": "session.open", "data": {"session": handle, "workspace": workspace, "mode": mode}, "exit_code": 0}
                    if request_id is not None: payload["id"] = request_id
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    if request_id is not None:
                        with _SESSION_LOCK:
                            _RPC_REPLAYS[request_id] = (encoded,)
                            _RPC_FINGERPRINTS[request_id] = fingerprint
                            while len(_RPC_REPLAYS) > 1024: _RPC_REPLAYS.pop(next(iter(_RPC_REPLAYS)))
                    yield encoded
                    continue
                if method in {"session.close", "session.status", "session.events", "session.cancel", "session.pause", "session.resume", "session.approval"}:
                    handle = params.get("session") or params.get("session_id")
                    with _SESSION_LOCK: _prune_sessions()
                    if handle not in _RPC_SESSIONS:
                        restored = _load_session(handle, params.get("workspace"))
                        if restored is not None:
                            with _SESSION_LOCK: _RPC_SESSIONS[handle] = restored
                    if not isinstance(handle, str) or handle not in _RPC_SESSIONS:
                        raise ValueError("session handle is unknown")
                    with _SESSION_LOCK:
                        info = _RPC_SESSIONS.get(handle, {})
                        if info.get("mode") == "act":
                            try:
                                trusted = TrustStore(Path(info["workspace"])).status().get("trusted", False)
                            except TrustError:
                                trusted = False
                            if not trusted:
                                info["state"] = "trust_revoked"
                                raise ValueError("workspace trust is not granted")
                        if method in {"session.pause", "session.resume", "session.cancel", "session.approval"} and info.get("state") in {"completed", "failed", "cancelled", "approval_denied"}:
                            raise ValueError("session is terminal and cannot be controlled")
                        if method == "session.close" and info.get("state") == "running":
                            raise ValueError("session is busy; cancel or await completion before close")
                        if method == "session.cancel":
                            info["state"] = "cancelled"
                            info["cancel_requested"] = True
                        elif method == "session.pause": info["state"] = "paused"
                        elif method == "session.resume": info["state"] = "running"
                        elif method == "session.approval":
                            decision = params.get("approved")
                            if not isinstance(decision, bool): raise ValueError("session.approval.approved must be boolean")
                            info["state"] = "running" if decision else "approval_denied"
                        if method in {"session.cancel", "session.pause", "session.resume", "session.approval"}:
                            info["sequence"] = int(info.get("sequence", 0)) + 1
                            info.setdefault("events", []).append({"sequence": info["sequence"], "type": method.rsplit(".", 1)[-1], "state": info.get("state")})
                            if len(info["events"]) > _MAX_SESSION_EVENTS:
                                del info["events"][:-_MAX_SESSION_EVENTS]
                        if method == "session.close":
                            _remove_session_record(handle, info)
                            _RPC_SESSIONS.pop(handle, None)
                        elif method in {"session.cancel", "session.pause", "session.resume", "session.approval"}:
                            _persist_session(handle, info)
                    data = {"session": handle, "closed": method == "session.close", "state": info.get("state"), "sequence": info.get("sequence", 0), "workspace": info.get("workspace"), "mode": info.get("mode"), "cancel_requested": bool(info.get("cancel_requested", False))}
                    if method == "session.events":
                        after = params.get("after", 0)
                        limit = params.get("limit", 100)
                        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
                            raise ValueError("session.events.after must be a non-negative integer")
                        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                            raise ValueError("session.events.limit must be a positive integer")
                        events = [item for item in info.get("events", []) if int(item.get("sequence", 0)) > after]
                        data["events"] = events[: min(limit, 100)]
                        data["next_sequence"] = int(data["events"][-1]["sequence"]) if data["events"] else after
                        retained = info.get("events", [])
                        oldest = int(retained[0].get("sequence", 0)) if retained else int(info.get("sequence", 0)) + 1
                        data["oldest_sequence"] = oldest
                        data["truncated"] = bool(retained) and after < oldest - 1
                    payload = {"schema_version": 1, "kind": "session", "ok": True, "command": method, "data": data, "exit_code": 0}
                    if request_id is not None: payload["id"] = request_id
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    if request_id is not None:
                        with _SESSION_LOCK:
                            _RPC_REPLAYS[request_id] = (encoded,)
                            _RPC_FINGERPRINTS[request_id] = fingerprint
                            while len(_RPC_REPLAYS) > 1024: _RPC_REPLAYS.pop(next(iter(_RPC_REPLAYS)))
                    yield encoded
                    continue
                if method in {"run", "session.run"}:
                    prompt = params.get("prompt", "")
                    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8_000:
                        raise ValueError("run.prompt must be non-empty and bounded")
                    global_args = []
                    argv_value.append(prompt)
                    handle = params.get("session") or params.get("session_id")
                    if method == "session.run" and not handle:
                        raise ValueError("session.run requires session handle")
                    if handle:
                        with _SESSION_LOCK:
                            info = _RPC_SESSIONS.get(handle)
                        if info is None: raise ValueError("session handle is unknown")
                        with _SESSION_LOCK:
                            state = info.get("state")
                            if state in {"approval_denied", "cancelled", "completed", "failed"}:
                                raise ValueError(f"session is {state}; open a new session")
                            if state == "running":
                                raise ValueError("session is busy; wait for the active run to finish")
                        if info.get("mode") == "act":
                            try:
                                trusted = TrustStore(Path(info["workspace"])).status().get("trusted", False)
                            except TrustError:
                                trusted = False
                            if not trusted: raise ValueError("workspace trust is not granted")
                        with _SESSION_LOCK:
                            info["state"] = "running"
                        # A new explicit run clears a prior cancellation latch;
                        # the previous run remains recorded as cancelled.
                        with _SESSION_LOCK:
                            info["cancel_requested"] = False
                        global_args.extend(["--workspace", info["workspace"]])
                        argv_value.extend(["--mode", info["mode"], "--session", info["session_path"]])
                    for key, flag in (("workspace", "--workspace"), ("mode", "--mode"), ("session", "--session"), ("profile", "--profile")):
                        value = params.get(key)
                        if value is not None:
                            if not isinstance(value, str) or len(value) > 1_000 or any(ch in value for ch in "\r\n"):
                                raise ValueError(f"run.{key} is invalid")
                            (global_args if key == "workspace" else argv_value).extend([flag, value])
                    if params.get("auto_approve") is True: argv_value.append("--auto-approve")
                    if params.get("require_trust") is True: argv_value.append("--require-trust")
                    if params.get("demo") is True: argv_value.append("--demo")
                    argv_value = global_args + argv_value
                elif method == "login":
                    provider = params.get("provider")
                    api_key_env = params.get("api_key_env")
                    if provider is not None:
                        if not isinstance(provider, str) or not provider.strip() or len(provider) > 64:
                            raise ValueError("login.provider is invalid")
                        argv_value.extend(["--provider", provider])
                    if api_key_env is not None:
                        if not isinstance(api_key_env, str) or not api_key_env.strip() or len(api_key_env) > 128 or any(ch in api_key_env for ch in "\r\n"):
                            raise ValueError("login.api_key_env is invalid")
                        argv_value.extend(["--api-key-env", api_key_env])
                    profile = params.get("profile")
                    if profile is not None:
                        if not isinstance(profile, str) or not profile.strip() or len(profile) > 128 or any(ch in profile for ch in "\r\n"):
                            raise ValueError("login.profile is invalid")
                        argv_value.extend(["--profile", profile])
                elif method.startswith("session."):
                    session_id = params.get("session") or params.get("session_id")
                    if method == "session.tree":
                        limit = params.get("limit", 50)
                        if isinstance(limit, bool) or not isinstance(limit, int): raise ValueError("session.tree limit must be an integer")
                        argv_value.extend(["--limit", str(max(1, min(200, limit)))])
                    else:
                        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512: raise ValueError("session parameter is required and bounded")
                        argv_value.append(session_id)
                argv_value.append("--jsonl")
            if not isinstance(argv_value, list):
                raise ValueError("argv must be an array")
            argv = [str(item) for item in argv_value]
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = main(argv)
            if method in {"run", "session.run"} and handle:
                with _SESSION_LOCK:
                    info = _RPC_SESSIONS.get(handle)
                    if info is not None:
                        info["state"] = "completed" if code == 0 else ("cancelled" if code == 130 else "failed")
                        info["sequence"] = int(info.get("sequence", 0)) + 1
                        info.setdefault("events", []).append({"sequence": info["sequence"], "type": "run_finished", "state": info["state"], "exit_code": code})
                        _persist_session(handle, info)
            output = [item for item in captured.getvalue().splitlines() if item.strip()]
            if not output:
                payload = {"schema_version": 1, "kind": "result", "ok": code == 0, "command": "rpc", "data": {}, "exit_code": code}
                if request_id is not None: payload["id"] = request_id
                encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if request_id is not None:
                    with _SESSION_LOCK:
                        _RPC_REPLAYS[request_id] = (encoded,)
                        _RPC_FINGERPRINTS[request_id] = fingerprint
                        while len(_RPC_REPLAYS) > 1024: _RPC_REPLAYS.pop(next(iter(_RPC_REPLAYS)))
                yield encoded
                continue
            responses: list[str] = []
            for index, raw in enumerate(output):
                envelope: Any = json.loads(raw)
                if isinstance(envelope, dict):
                    envelope.setdefault("exit_code", code if index == len(output) - 1 else 0)
                    if request_id is not None: envelope["id"] = request_id
                    if method is not None: envelope["method"] = method
                responses.append(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
            if request_id is not None:
                with _SESSION_LOCK:
                    _RPC_REPLAYS[request_id] = tuple(responses)
                    _RPC_FINGERPRINTS[request_id] = fingerprint
                    while len(_RPC_REPLAYS) > 1024: _RPC_REPLAYS.pop(next(iter(_RPC_REPLAYS)))
            yield from responses
        except Exception as exc:
            message = str(exc)[:2000]
            if "request_too_large" in message:
                code = "request_too_large"
            elif "workspace trust" in message:
                code = "trust_revoked"
            elif "busy" in message:
                code = "session_busy"
            elif "approval was denied" in message:
                code = "approval_denied"
            elif "session is " in message and "open a new session" in message:
                code = "session_terminal"
            else:
                code = "invalid_request"
            if method in {"run", "session.run"} and handle:
                with _SESSION_LOCK:
                    info = _RPC_SESSIONS.get(handle)
                    if info is not None and info.get("state") == "running":
                        info["state"] = "failed"
                        info["sequence"] = int(info.get("sequence", 0)) + 1
                        info.setdefault("events", []).append({"sequence": info["sequence"], "type": "run_failed", "state": "failed", "error_code": code})
                        try: _persist_session(handle, info)
                        except OSError: pass
            payload = {"schema_version": 1, "kind": "error", "ok": False, "command": "rpc", "error": {"code": code, "message": message}, "exit_code": 2}
            if request_id is not None: payload["id"] = request_id
            if method is not None: payload["method"] = method
            yield json.dumps(payload, ensure_ascii=False)


__all__ = ["serve_lines"]
