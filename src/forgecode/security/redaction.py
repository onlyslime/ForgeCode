"""Shared, bounded redaction helpers for user-visible and persisted text."""

from __future__ import annotations

import re
from typing import Any, Iterable


_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_NAMED_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|token|password|secret|cookie|authorization)\b"
    r"(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_SENSITIVE_KEY_PARTS = ("api_key", "api-key", "apikey", "authorization", "token", "password", "secret", "cookie", "credential")


def redact_text(value: object, secrets: Iterable[str] = ()) -> str:
    """Redact configured values and common credential-shaped text."""
    rendered = str(value)
    for secret in sorted((secret for secret in secrets if secret), key=len, reverse=True):
        rendered = rendered.replace(secret, "[REDACTED]")
    rendered = _BEARER_RE.sub("Bearer [REDACTED]", rendered)
    rendered = _NAMED_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", rendered)
    return rendered


def redact_value(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact structured values before model/session exposure."""
    secret_values = tuple(secret for secret in secrets if secret)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SENSITIVE_KEY_PARTS):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_value(item, secret_values)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, secret_values)
