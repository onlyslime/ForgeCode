"""Structured verification outcomes shared by the loop and CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VerificationResult:
    command: str
    attempt: int
    risk: str | None
    approval: str | None
    exit_code: int | None
    timed_out: bool
    duration_seconds: float | None
    stdout: str
    stderr: str
    failure_summary: str | None
    changed_files: tuple[str, ...] = ()
    next_action: str = "none"
    conflict: bool = False
    ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["VerificationResult"]
