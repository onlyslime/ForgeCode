"""Application-level run assembly shared by CLI and interactive clients."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..agent import AgentConfig, AgentLoop, ContextBuilder, LoopResult
from ..config import EffectiveConfig
from ..models import ModelProvider
from ..security.workspace import WorkspaceGuard
from ..storage import SessionStore, TransactionStore
from ..tools import ToolContext, ToolRegistry
from ..hooks import HookRegistry


@dataclass(frozen=True)
class RunService:
    """Construct an AgentLoop without printing or owning CLI policy."""

    provider: ModelProvider
    registry: ToolRegistry
    guard: WorkspaceGuard
    session: SessionStore
    config: AgentConfig = AgentConfig()
    effective_config: EffectiveConfig | None = None
    approval: Any | None = None
    transaction_store: TransactionStore | None = None
    plan_id: str | None = None
    plan_item_id: str | None = None
    rules_fingerprint: str = ""
    plan_fingerprint: str = ""
    config_fingerprint: str = ""
    pre_side_effect_check: Callable[[], bool | str] | None = None
    hooks: HookRegistry | None = None

    async def execute(self, prompt: str, *, mode: str = "act", secrets: tuple[str, ...] = (), on_event: Callable[[str, dict[str, Any]], None] | None = None) -> LoopResult:
        transaction_store = self.transaction_store or TransactionStore(self.guard, max_total_bytes=self.effective_config.transaction_max_bytes if self.effective_config else 50_000_000)
        context = ToolContext(self.guard, self.approval, mode=mode, secrets=secrets, transaction_store=transaction_store, run_id=self.session.run_id, plan_id=self.plan_id, plan_item_id=self.plan_item_id, rules_fingerprint=self.rules_fingerprint, plan_fingerprint=self.plan_fingerprint, config_fingerprint=self.config_fingerprint, pre_side_effect_check=self.pre_side_effect_check, hooks=self.hooks)
        context_builder = ContextBuilder(max_chars=self.effective_config.context_budget_chars if self.effective_config else 60_000)
        loop = AgentLoop(self.provider, self.registry, context, session=self.session, config=self.config, context_builder=context_builder, on_event=on_event)
        return await loop.run(prompt)


__all__ = ["RunService"]
