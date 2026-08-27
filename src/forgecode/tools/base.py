"""Small provider-neutral tool protocol."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..security.workspace import WorkspaceGuard


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolContext:
    guard: WorkspaceGuard
    approval: "ApprovalPolicy | None" = None


class ApprovalPolicy(Protocol):
    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return whether a side-effecting operation may run."""


class Tool(Protocol):
    definition: ToolDefinition

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.definition.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.definition.name}")
        self._tools[tool.definition.name] = tool

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {"name": definition.name, "description": definition.description, "parameters": definition.parameters}
            for definition in self.definitions()
        ]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"unknown tool: {name}", {"error": "unknown_tool"})
        try:
            return tool.execute(arguments, context)
        except Exception as exc:  # tool errors become model context, never process crashes
            return ToolResult(False, f"{type(exc).__name__}: {exc}", {"error": type(exc).__name__})
