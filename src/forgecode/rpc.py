"""Line-delimited RPC bridge for programmatic clients.

Each request is a JSON object containing ``argv`` (CLI arguments excluding the
program name). The response is the same single ForgeCode JSON envelope emitted
by the CLI, preserving command semantics and exit codes.
"""
from __future__ import annotations

import contextlib
import io
import json
from typing import Any, Iterable

from .application.commands import main


def serve_lines(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get("argv", []), list):
                raise ValueError("request must be an object with argv array")
            request_id = request.get("id")
            if request_id is not None and (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)):
                raise ValueError("request id must be a string or integer")
            argv_value = request.get("argv", [])
            method = request.get("method")
            if method is not None:
                if not isinstance(method, str) or len(method) > 128 or any(ch.isspace() for ch in method):
                    raise ValueError("method must be bounded non-whitespace text")
                method_map = {"trust.status": ["trust", "status"], "trust.grant": ["trust", "grant"], "trust.revoke": ["trust", "revoke"], "provider.list": ["provider", "list"], "provider.health": ["provider", "health"], "config.show": ["config", "show"], "doctor": ["doctor"], "login": ["login"]}
                if method not in method_map:
                    raise ValueError("unsupported RPC method")
                if argv_value:
                    raise ValueError("method and argv cannot be combined")
                argv_value = [*method_map[method], "--jsonl"]
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
