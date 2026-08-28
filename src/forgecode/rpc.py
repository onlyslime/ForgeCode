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
            argv = [str(item) for item in request.get("argv", [])]
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = main(argv)
            output = [item for item in captured.getvalue().splitlines() if item.strip()]
            if not output:
                yield json.dumps({"schema_version": 1, "kind": "result", "ok": code == 0, "command": "rpc", "data": {}, "exit_code": code}, ensure_ascii=False, separators=(",", ":"))
                continue
            for index, raw in enumerate(output):
                envelope: Any = json.loads(raw)
                if isinstance(envelope, dict):
                    envelope.setdefault("exit_code", code if index == len(output) - 1 else 0)
                yield json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:
            yield json.dumps({"schema_version": 1, "kind": "error", "ok": False, "command": "rpc", "error": {"code": "invalid_request", "message": str(exc)[:2000]}, "exit_code": 2}, ensure_ascii=False)


__all__ = ["serve_lines"]
