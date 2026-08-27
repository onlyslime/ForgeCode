"""Small provider-neutral tool protocol and execution-mode boundary."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol

from ..security.redaction import redact_text, redact_value
from ..security.workspace import WorkspaceGuard


class AgentMode(StrEnum):
    PLAN = "plan"
    ACT = "act"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    side_effecting: bool = False


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
    mode: AgentMode | str = AgentMode.ACT
    secrets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", AgentMode(self.mode))
        object.__setattr__(self, "secrets", tuple(secret for secret in self.secrets if isinstance(secret, str) and secret))

    def request_approval(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        approved = self.approval is not None and self.approval.approve(tool_name, arguments)
        if self.approval_observer:
            self.approval_observer(tool_name, arguments, approved)
        return approved

    def deny_if_plan(self, tool_name: str) -> ToolResult | None:
        """Defence in depth for callers that invoke a tool outside the registry."""
        if self.mode is AgentMode.PLAN:
            return ToolResult(
                False,
                f"{tool_name} is unavailable in plan mode; switch to act mode to perform side effects",
                {"error": "mode_denied", "mode": self.mode.value, "tool": tool_name},
            )
        return None


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

    def definitions(self, mode: AgentMode | str | None = None) -> tuple[ToolDefinition, ...]:
        definitions = tuple(tool.definition for tool in self._tools.values())
        if mode is None or AgentMode(mode) is AgentMode.ACT:
            return definitions
        return tuple(definition for definition in definitions if not definition.side_effecting)

    def schemas(self, mode: AgentMode | str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.parameters,
                },
            }
            for definition in self.definitions(mode)
        ]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"unknown tool: {name}", {"error": "unknown_tool"})
        if context.mode is AgentMode.PLAN and tool.definition.side_effecting:
            return ToolResult(
                False,
                f"{name} is unavailable in plan mode; switch to act mode to perform side effects",
                {"error": "mode_denied", "mode": context.mode.value, "tool": name},
            )
        if not isinstance(arguments, dict):
            return ToolResult(False, "tool arguments must be an object", {"error": "invalid_arguments"})
        try:
            result = tool.execute(arguments, context)
            if not isinstance(result, ToolResult):
                return ToolResult(False, "tool returned an invalid result", {"error": "invalid_tool_result"})
            if not isinstance(result.output, str):
                return ToolResult(False, "tool returned non-text output", {"error": "invalid_tool_result"})
            safe_output = redact_text(result.output, context.secrets)
            safe_metadata = redact_value(result.metadata, context.secrets)
            if len(safe_output) <= self.max_output_chars:
                return ToolResult(result.ok, safe_output, safe_metadata)
            metadata = {**safe_metadata, "truncated": True, "original_output_chars": len(safe_output)}
            return ToolResult(result.ok, safe_output[: self.max_output_chars] + "\n[tool output truncated]", metadata)
        except Exception as exc:  # tool errors become model context, never process crashes
            return ToolResult(False, redact_text(f"{type(exc).__name__}: {exc}", context.secrets), {"error": type(exc).__name__})
