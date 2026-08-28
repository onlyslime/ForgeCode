"""Small provider-neutral tool protocol and execution-mode boundary."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol
import time
import math

from ..security.redaction import redact_text, redact_value
from ..security.workspace import WorkspaceGuard
from ..models.protocol import CancellationToken


class AgentMode(StrEnum):
    PLAN = "plan"
    ACT = "act"


class PauseRequested(RuntimeError):
    """Internal signal that a non-interactive run reached a pause boundary."""


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
    deadline_monotonic: float | None = None
    cancellation_requested: Callable[[], bool] | None = None
    cancellation_token: CancellationToken | None = None
    transaction_store: Any | None = None
    run_id: str = ""
    plan_id: str | None = None
    plan_item_id: str | None = None
    pre_side_effect_check: Callable[[], bool | str] | None = None
    rules_fingerprint: str = ""
    plan_fingerprint: str = ""
    config_fingerprint: str = ""
    hooks: Any | None = None
    correlation_id: str | None = None
    # Optional synchronous gate owned by AgentLoop.  It is invoked after an
    # approval decision and before a side-effecting tool mutates anything, so
    # an interactive pause cannot race approval into execution.
    pause_wait: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", AgentMode(self.mode))
        object.__setattr__(self, "secrets", tuple(secret for secret in self.secrets if isinstance(secret, str) and secret))
        if self.deadline_monotonic is not None and (isinstance(self.deadline_monotonic, bool) or not isinstance(self.deadline_monotonic, (int, float)) or not math.isfinite(self.deadline_monotonic)):
            raise ValueError("deadline_monotonic must be a finite number or None")

    def remaining_seconds(self, requested: float) -> float:
        if isinstance(requested, bool) or not isinstance(requested, (int, float)) or not math.isfinite(requested) or requested < 0:
            raise ValueError("requested timeout must be a finite non-negative number")
        if self.deadline_monotonic is None:
            return float(requested)
        return max(0.0, min(float(requested), self.deadline_monotonic - time.monotonic()))

    @property
    def cancelled(self) -> bool:
        token_cancelled = bool(self.cancellation_token and self.cancellation_token.is_cancelled())
        try:
            callback_cancelled = bool(self.cancellation_requested and self.cancellation_requested())
        except Exception:
            callback_cancelled = self.cancellation_requested is not None
        return token_cancelled or callback_cancelled

    @property
    def cancellation_reason(self) -> str:
        if self.cancellation_token is not None and self.cancellation_token.is_cancelled():
            return self.cancellation_token.reason
        return "cancelled"

    def request_approval(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        approved = self.approval is not None and self.approval.approve(tool_name, arguments)
        if self.approval_observer:
            self.approval_observer(tool_name, arguments, approved)
        if approved and self.pause_wait is not None:
            self.pause_wait()
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

    def deny_if_stale(self, tool_name: str) -> ToolResult | None:
        """Recheck rules/config/context immediately before a side effect."""
        if self.pre_side_effect_check is None:
            return None
        try:
            result = self.pre_side_effect_check()
        except Exception as exc:
            return ToolResult(False, f"{tool_name} blocked because policy context could not be revalidated", {"error": "context_revalidation_failed", "detail": type(exc).__name__})
        if result is True:
            return None
        reason = result if isinstance(result, str) and result else "rules/config/context changed after planning"
        return ToolResult(False, f"{tool_name} blocked: {reason}", {"error": "stale_context", "reason": reason})


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

    def filter(self, policy: Any | None = None) -> "ToolRegistry":
        """Return a registry narrowed by policy; policy cannot add tools."""
        if policy is None:
            return self
        result = ToolRegistry(max_output_chars=self.max_output_chars)
        available = set(self._tools)
        for name, tool in self._tools.items():
            permits = getattr(policy, "permits", None)
            if permits is None or permits(name, available=available):
                result.register(tool)
        return result

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
        # A provider can return a tool call at the same moment another thread
        # requests cancellation. Check at the executor boundary so a
        # side-effecting tool is never entered after cancellation was observed.
        if context.cancelled:
            return ToolResult(False, f"{name} cancelled before execution", {"error": "cancelled", "cancellation_reason": context.cancellation_reason, "tool": name})
        if context.mode is AgentMode.PLAN and getattr(tool.definition, "side_effecting", False):
            return ToolResult(
                False,
                f"{name} is unavailable in plan mode; switch to act mode to perform side effects",
                {"error": "mode_denied", "mode": context.mode.value, "tool": name},
            )
        if not isinstance(arguments, dict):
            return ToolResult(False, "tool arguments must be an object", {"error": "invalid_arguments"})
        before_hook_issues = ()
        if context.hooks is not None:
            hook_correlation = context.correlation_id or f"{context.run_id or 'run'}:tool:{name}"
            before_hook_issues = context.hooks.emit("before_tool", {"tool": name, "arguments": arguments, "mode": context.mode.value}, cancellation=context.cancellation_token or context.cancellation_requested, correlation_id=hook_correlation)
            blocked = [issue for issue in before_hook_issues if issue.blocked]
            if blocked:
                return ToolResult(False, f"{name} blocked by lifecycle hook", {"error": "hook_blocked", "hook_issues": [issue.to_dict() for issue in before_hook_issues]})
            if context.cancelled:
                return ToolResult(False, f"{name} cancelled before execution", {"error": "cancelled", "cancellation_reason": context.cancellation_reason, "tool": name})
        if getattr(tool.definition, "side_effecting", False) and context.pause_wait is not None:
            try:
                context.pause_wait()
            except PauseRequested:
                return ToolResult(False, f"{name} paused before execution", {"error": "paused", "tool": name})
        if context.cancelled:
            return ToolResult(False, f"{name} cancelled before execution", {"error": "cancelled", "cancellation_reason": context.cancellation_reason, "tool": name})
        try:
            result = tool.execute(arguments, context)
            if not isinstance(result, ToolResult):
                return ToolResult(False, "tool returned an invalid result", {"error": "invalid_tool_result"})
            if not isinstance(result.output, str):
                return ToolResult(False, "tool returned non-text output", {"error": "invalid_tool_result"})
            safe_output = redact_text(result.output, context.secrets)
            safe_metadata = redact_value(result.metadata, context.secrets)
            if len(safe_output) <= self.max_output_chars:
                final = ToolResult(result.ok, safe_output, safe_metadata)
            else:
                metadata = {**safe_metadata, "truncated": True, "original_output_chars": len(safe_output)}
                final = ToolResult(result.ok, safe_output[: self.max_output_chars] + "\n[tool output truncated]", metadata)
            if context.hooks is not None:
                after_hook_issues = context.hooks.emit("after_tool", {"tool": name, "ok": final.ok, "output": final.output[:4_000], "metadata": final.metadata}, cancellation=context.cancellation_token or context.cancellation_requested, correlation_id=context.correlation_id or f"{context.run_id or 'run'}:tool:{name}")
                if before_hook_issues or after_hook_issues:
                    final = ToolResult(final.ok, final.output, {**final.metadata, "hook_issues": [issue.to_dict() for issue in (*before_hook_issues, *after_hook_issues)]})
                blocked = [issue for issue in after_hook_issues if issue.blocked]
                if blocked:
                    # The operation has already returned (and may have
                    # committed a transaction).  Do not claim that a
                    # side-effect was prevented; retain the real result and
                    # expose the fail-closed issue for the caller's audit.
                    return ToolResult(final.ok, final.output, {**final.metadata, "error": "hook_failed_after_effect", "hook_issues": [issue.to_dict() for issue in after_hook_issues]})
            return final
        except PauseRequested:
            return ToolResult(False, f"{name} paused before execution", {"error": "paused", "tool": name})
        except Exception as exc:  # tool errors become model context, never process crashes
            final = ToolResult(False, redact_text(f"{type(exc).__name__}: {exc}", context.secrets), {"error": type(exc).__name__})
            if context.hooks is not None:
                after_hook_issues = context.hooks.emit("after_tool", {"tool": name, "ok": False, "error": type(exc).__name__}, cancellation=context.cancellation_token or context.cancellation_requested, correlation_id=context.correlation_id or f"{context.run_id or 'run'}:tool:{name}")
                if before_hook_issues or after_hook_issues:
                    final = ToolResult(final.ok, final.output, {**final.metadata, "hook_issues": [issue.to_dict() for issue in (*before_hook_issues, *after_hook_issues)]})
            return final
