"""Small defensive helpers for decoding untrusted JSON.

The standard-library decoder is recursive for nested arrays/objects.  Runtime
files and provider responses are untrusted, so scan their shape iteratively
before decoding and normalize recursion failures into an ordinary ``ValueError``
that each caller can translate to its own public error type.
"""

from __future__ import annotations

import json
from typing import Any, Callable


MAX_JSON_DEPTH = 256
MAX_JSON_NODES = 100_000


def reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def json_shape_issue(value: str | bytes | bytearray, *, max_depth: int = MAX_JSON_DEPTH, max_nodes: int = MAX_JSON_NODES) -> str | None:
    """Return a bounded nesting/node diagnostic without recursive parsing."""
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            # Let ``json.loads`` produce its normal UTF-8 diagnostic.
            return None
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError("JSON input must be text or bytes")
    depth = 0
    nodes = 0
    in_string = False
    escaped = False
    token_active = False

    def count_node() -> str | None:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            return f"JSON structure exceeds the {max_nodes}-node safety limit"
        return None

    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            issue = count_node()
            if issue is not None:
                return issue
            in_string = True
            token_active = False
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                return f"JSON nesting exceeds the {max_depth}-level safety limit"
            issue = count_node()
            if issue is not None:
                return issue
            token_active = False
        elif character in "]}":
            # Syntax correctness remains the decoder's responsibility.  Do
            # not let malformed closers make the pre-scan itself unsafe.
            depth = max(0, depth - 1)
            token_active = False
        elif character in ":," or character.isspace():
            token_active = False
        elif not token_active:
            # Count primitive JSON values (numbers, true/false/null) as well
            # as containers and strings.  Counting only ``[{`` would let a
            # flat array with millions of scalars evade the node budget.
            issue = count_node()
            if issue is not None:
                return issue
            token_active = True
    return None


def bounded_json_loads(
    value: str | bytes | bytearray,
    *,
    parse_constant: Callable[[str], Any] = reject_nonfinite,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
) -> Any:
    """Decode a bounded JSON value and never leak ``RecursionError``."""
    issue = json_shape_issue(value, max_depth=max_depth, max_nodes=max_nodes)
    if issue is not None:
        raise ValueError(issue)
    try:
        return json.loads(value, parse_constant=parse_constant)
    except RecursionError as exc:
        raise ValueError(f"JSON nesting exceeds the {max_depth}-level safety limit") from exc


__all__ = ["MAX_JSON_DEPTH", "MAX_JSON_NODES", "bounded_json_loads", "json_shape_issue", "reject_nonfinite"]
