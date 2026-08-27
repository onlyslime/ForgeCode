"""Typed application facade for durable transaction review and undo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..storage.transaction import TransactionError, TransactionStore


@dataclass(frozen=True)
class TransactionService:
    store: TransactionStore

    def review(self, transaction_id: str = "latest") -> dict[str, Any]:
        return self.store.review(transaction_id)

    def undo_preview(self, transaction_id: str = "latest") -> dict[str, Any]:
        return self.store.preview_undo(transaction_id).to_dict()

    def undo(self, transaction_id: str, *, approval: Any, run_id: str) -> dict[str, Any]:
        manifest = self.store.latest() if transaction_id == "latest" else self.store.load(transaction_id)
        result = self.store.undo(manifest.transaction_id, approval=approval, run_id=run_id)
        return result.to_dict()


__all__ = ["TransactionService"]
