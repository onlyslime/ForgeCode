"""Line-delimited RPC bridge for programmatic clients.

Each request is a JSON object containing ``argv`` (CLI arguments excluding the
program name). The response is the same single ForgeCode JSON envelope emitted
by the CLI, preserving command semantics and exit codes.
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
import uuid
from typing import Any, Iterable

from .application.commands import main

_SESSION_LOCK = threading.RLock()
_RPC_SESSIONS: dict[str, dict[str, Any]] = {}
_RPC_REPLAYS: dict[str | int, tuple[str, ...]] = {}


def serve_lines(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get("argv", []), list):
                raise ValueError("request must be an object with argv array")
            request_id = request.get("id")
            if request_id is not None and (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)):
                raise ValueError("request id must be a string or integer")
            if request_id is not None:
                with _SESSION_LOCK:
                    replay = _RPC_REPLAYS.get(request_id)
                if replay is not None:
                    yield from replay
                    continue
            argv_value = request.get("argv", [])
            method = request.get("method")
            if method is not None:
                if not isinstance(method, str) or len(method) > 128 or any(ch.isspace() for ch in method):
                    raise ValueError("method must be bounded non-whitespace text")
                method_map = {"trust.status": ["trust", "status"], "trust.grant": ["trust", "grant"], "trust.revoke": ["trust", "revoke"], "provider.list": ["provider", "list"], "provider.health": ["provider", "health"], "config.show": ["config", "show"], "doctor": ["doctor"], "login": ["login"], "run": ["run"], "session.open": ["session", "open"], "session.run": ["run"], "session.events": ["session", "events"], "session.cancel": ["session", "cancel"], "session.pause": ["session", "pause"], "session.resume": ["session", "resume"], "session.close": ["session", "close"], "session.status": ["session", "status"], "session.inspect": ["session", "inspect"], "session.tree": ["session", "tree"], "session.export": ["session", "export"]}
                if method not in method_map:
                    raise ValueError("unsupported RPC method")
                if argv_value:
                    raise ValueError("method and argv cannot be combined")
                params = request.get("params", {})
                if params is None: params = {}
                if not isinstance(params, dict) or len(params) > 16:
                    raise ValueError("params must be a bounded object")
                argv_value = list(method_map[method])
                if method == "session.open":
                    workspace = params.get("workspace", ".")
                    mode = params.get("mode", "plan")
                    if not isinstance(workspace, str) or len(workspace) > 1_000 or any(ch in workspace for ch in "\r\n"):
                        raise ValueError("session.open.workspace is invalid")
                    if mode not in {"plan", "act"}:
                        raise ValueError("session.open.mode must be plan or act")
                    handle = uuid.uuid4().hex
                    with _SESSION_LOCK:
                        _RPC_SESSIONS[handle] = {"workspace": workspace, "mode": mode, "state": "idle", "sequence": 0, "events": []}
                    payload = {"schema_version": 1, "kind": "session", "ok": True, "command": "session.open", "data": {"session": handle, "workspace": workspace, "mode": mode}, "exit_code": 0}
                    if request_id is not None: payload["id"] = request_id
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    if request_id is not None:
                        with _SESSION_LOCK:
                            _RPC_REPLAYS[request_id] = (encoded,)
                            while len(_RPC_REPLAYS) > 1024: _RPC_REPLAYS.pop(next(iter(_RPC_REPLAYS)))
                    yield encoded
                    continue
                if method in {"session.close", "session.status", "session.events", "session.cancel", "session.pause", "session.resume"}:
                    handle = params.get("session") or params.get("session_id")
                    if not isinstance(handle, str) or handle not in _RPC_SESSIONS:
                        raise ValueError("session handle is unknown")
                    with _SESSION_LOCK:
                        info = _RPC_SESSIONS.get(handle, {})
                        if method == "session.cancel": info["state"] = "cancelled"
                        elif method == "session.pause": info["state"] = "paused"
                        elif method == "session.resume": info["state"] = "running"
                        if method in {"session.cancel", "session.pause", "session.resume"}:
                            info["sequence"] = int(info.get("sequence", 0)) + 1
                            info.setdefault("events", []).append({"sequence": info["sequence"], "type": method.rsplit(".", 1)[-1], "state": info.get("state")})
                        if method == "session.close": _RPC_SESSIONS.pop(handle, None)
                    data = {"session": handle, "closed": method == "session.close", "state": info.get("state"), "sequence": info.get("sequence", 0), "workspace": info.get("workspace"), "mode": info.get("mode")}
                    if method == "session.events": data["events"] = list(info.get("events", []))[-100:]
                    payload = {"schema_version": 1, "kind": "session", "ok": True, "command": method, "data": data, "exit_code": 0}
                    if request_id is not None: payload["id"] = request_id
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    if request_id is not None:
                        with _SESSION_LOCK:
                            _RPC_REPLAYS[request_id] = (encoded,)
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
                        global_args.extend(["--workspace", info["workspace"]])
                        argv_value.extend(["--mode", info["mode"], "--session", handle])
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
            output = [item for item in captured.getvalue().splitlines() if item.strip()]
            if not output:
                payload = {"schema_version": 1, "kind": "result", "ok": code == 0, "command": "rpc", "data": {}, "exit_code": code}
                if request_id is not None: payload["id"] = request_id
                yield json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                continue
            for index, raw in enumerate(output):
                envelope: Any = json.loads(raw)
                if isinstance(envelope, dict):
                    envelope.setdefault("exit_code", code if index == len(output) - 1 else 0)
                    if request_id is not None: envelope["id"] = request_id
                    if method is not None: envelope["method"] = method
                yield json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:
            yield json.dumps({"schema_version": 1, "kind": "error", "ok": False, "command": "rpc", "error": {"code": "invalid_request", "message": str(exc)[:2000]}, "exit_code": 2}, ensure_ascii=False)


__all__ = ["serve_lines"]
