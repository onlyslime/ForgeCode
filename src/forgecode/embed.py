"""Small in-process embedding API built on the public CLI/RPC envelope."""
from __future__ import annotations

import json
from typing import Any, Iterable

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
