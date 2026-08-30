"""Shared, bounded redaction helpers for user-visible and persisted text."""

from __future__ import annotations

import re
import math
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Iterable


_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_NAMED_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|token|password|secret|cookie|authorization)\b"
    r"(\s*[:=]\s*)"
    # The unquoted branch consumes a trailing bracket as part of the value.
    # Without it, inputs such as ``token=abc]`` leaked an extra delimiter
    # after replacement (``token=[REDACTED]]``).
    r"(?:\"[^\"]*\"|'[^']*'|\[[^\]]*\]|[^\s,;}]+)"
)
_SENSITIVE_KEY_PARTS = ("api_key", "api-key", "apikey", "authorization", "token", "password", "secret", "cookie", "credential")
_MAX_SECRETS = 64
_MAX_SECRET_CHARS = 4_096


def _normalize_secrets(secrets: Iterable[str]) -> tuple[str, ...]:
    values = tuple(secret for secret in secrets if isinstance(secret, str) and secret)
    if len(values) > _MAX_SECRETS or any(len(secret) > _MAX_SECRET_CHARS for secret in values):
        raise ValueError("secrets exceed redaction limits")
    return values


def redact_text(value: object, secrets: Iterable[str] = ()) -> str:
    """Redact configured values and common credential-shaped text."""
    secret_values = _normalize_secrets(secrets)
    rendered = str(value)
    # Use a private sentinel while applying the bearer rule. This prevents a
    # bearer token following ``authorization=`` from being consumed as the
    # named value while also avoiding a second match on ``[REDACTED]``.
    bearer_sentinel = "\x00FORGECODE_BEARER_REDACTED\x00"
    rendered = _BEARER_RE.sub(bearer_sentinel, rendered)
    rendered = _NAMED_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", rendered)
    rendered = rendered.replace(bearer_sentinel, "Bearer [REDACTED]")
    for secret in sorted(secret_values, key=len, reverse=True):
        rendered = rendered.replace(secret, "[REDACTED]")
    return rendered


def redact_value(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact structured values before model/session exposure.

    This walker is intentionally defensive because metadata may contain
    dataclasses, paths, bytes, exceptions or cyclic containers supplied by a
    provider/tool implementation.  It always returns JSON-compatible values
    and never follows an object graph indefinitely.
    """
    secret_values = _normalize_secrets(secrets)
    active: set[int] = set()

    def walk(item: Any, depth: int = 0) -> Any:
        if depth > 20:
            return "[maximum nesting depth exceeded]"
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else "[non-finite number omitted]"
        if isinstance(item, str):
            return redact_text(item, secret_values)
        if isinstance(item, (bytes, bytearray, memoryview)):
            return f"[bytes omitted: {len(item)} bytes]"
        if isinstance(item, Path):
            return redact_text(item.as_posix(), secret_values)
        object_id = id(item)
        if object_id in active:
            return "[circular reference omitted]"
        active.add(object_id)
        try:
            if isinstance(item, dict):
                result: dict[str, Any] = {}
                for key, child in list(item.items())[:200]:
                    key_text = str(key)
                    if any(part in key_text.lower() for part in _SENSITIVE_KEY_PARTS):
                        result[key_text] = "[REDACTED]"
                    else:
                        result[key_text] = walk(child, depth + 1)
                if len(item) > 200:
                    result["_truncated_items"] = len(item) - 200
                return result
            if isinstance(item, (list, tuple, set, frozenset)):
                values = list(item)
                if isinstance(item, (set, frozenset)):
                    values.sort(key=repr)
                result = [walk(child, depth + 1) for child in values[:200]]
                if len(values) > 200:
                    result.append({"_truncated_items": len(values) - 200})
                return result
            if is_dataclass(item) and not isinstance(item, type):
                return {field.name: walk(getattr(item, field.name), depth + 1) for field in fields(item)[:200]}
            if isinstance(item, BaseException):
                return {"type": type(item).__name__, "message": redact_text(str(item), secret_values)}
            return redact_text(item, secret_values)
        finally:
            active.discard(object_id)

    return walk(value)
