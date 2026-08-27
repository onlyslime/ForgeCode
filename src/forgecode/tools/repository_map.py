"""Read-only repository map tool."""

from typing import Any

from ..context.repository import RepositoryMapBuilder
from .base import ToolContext, ToolDefinition, ToolResult


class RepositoryMapTool:
    definition = ToolDefinition(
        "repository_map",
        "Build a bounded deterministic repository map with language, build, tests, symbols and omissions.",
        {"type": "object", "properties": {"task": {"type": "string"}, "budget_chars": {"type": "integer"}}, "required": []},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        task = arguments.get("task", "repository inspection")
        budget = arguments.get("budget_chars", 20_000)
        if isinstance(budget, bool) or not isinstance(budget, int):
            raise ValueError("budget_chars must be an integer")
        repository = RepositoryMapBuilder(context.guard).build()
        plan = repository.plan_context(task, budget_chars=budget)
        return ToolResult(True, plan.render(), {"snapshot": repository.to_dict(), "selected_paths": list(plan.selected_paths), "omitted": plan.omitted, "budget_chars": plan.budget_chars})
