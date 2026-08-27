"""Small provider-neutral tool protocol."""

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

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
    approval_observer: Callable[[str, dict[str, Any], bool], None] | None = None

    def request_approval(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        approved = self.approval is not None and self.approval.approve(tool_name, arguments)
        if self.approval_observer:
            self.approval_observer(tool_name, arguments, approved)
        return approved


class ApprovalPolicy(Protocol):
    def approve(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return whether a side-effecting operation may run."""


class Tool(Protocol):
    definition: ToolDefinition

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self, *, max_output_chars: int = 20_000):
        self._tools: dict[str, Tool] = {}
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        self.max_output_chars = max_output_chars

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
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.parameters,
                },
            }
            for definition in self.definitions()
        ]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"unknown tool: {name}", {"error": "unknown_tool"})
        if not isinstance(arguments, dict):
            return ToolResult(False, "tool arguments must be an object", {"error": "invalid_arguments"})
        try:
            result = tool.execute(arguments, context)
            if not isinstance(result, ToolResult):
                return ToolResult(False, "tool returned an invalid result", {"error": "invalid_tool_result"})
            if not isinstance(result.output, str):
                return ToolResult(False, "tool returned non-text output", {"error": "invalid_tool_result"})
            if len(result.output) <= self.max_output_chars:
                return result
            metadata = {**result.metadata, "truncated": True, "original_output_chars": len(result.output)}
            return ToolResult(result.ok, result.output[: self.max_output_chars] + "\n[tool output truncated]", metadata)
        except Exception as exc:  # tool errors become model context, never process crashes
            return ToolResult(False, f"{type(exc).__name__}: {exc}", {"error": type(exc).__name__})
