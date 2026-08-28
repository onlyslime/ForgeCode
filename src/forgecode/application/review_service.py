"""Application facade for evidence-driven review reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..review import ReviewBuilder, ReviewReport, export_review, import_review
from ..security.workspace import WorkspaceGuard


@dataclass(frozen=True)
class ReviewService:
    """Build, export and verify reports without exposing runtime blobs."""

    guard: WorkspaceGuard
    secrets: tuple[str, ...] = ()
    max_files: int = 256
    max_commands: int = 128

    def build(self, *, session: Path | str | None = None, transaction_id: str = "latest") -> ReviewReport:
        return ReviewBuilder(self.guard, secrets=self.secrets, max_files=self.max_files, max_commands=self.max_commands).build(session=session, transaction_id=transaction_id)

    def review(self, *, session: Path | str | None = None, transaction_id: str = "latest") -> dict[str, Any]:
        return self.build(session=session, transaction_id=transaction_id).to_dict()

    def export(self, report: ReviewReport, path: Path | str) -> Path:
        return export_review(report, path, self.guard)

    def import_report(self, path: Path | str, *, verify_files: bool = True) -> ReviewReport:
        return import_review(path, self.guard, verify_files=verify_files)


__all__ = ["ReviewService"]
