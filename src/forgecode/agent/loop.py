"""The provider-neutral model -> tool -> result loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from ..models import Message, ModelProvider, ProviderError, is_valid_response
from ..storage import SessionStore
from ..tools import AgentMode, ToolContext, ToolRegistry
from .context import ContextBuilder


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 12
    max_repeated_calls: int = 2
    verification_command: str | None = None
    max_verification_attempts: int = 2


@dataclass(frozen=True)
class LoopResult:
    messages: tuple[Message, ...]
    stopped_reason: str
    error: str | None = None
    verification_ok: bool | None = None
    mode: str = AgentMode.ACT.value
    plan_summary: str | None = None
    explored: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.stopped_reason == "model_finished" and self.error is None


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        context: ToolContext,
        session: SessionStore | None = None,
        config: AgentConfig | None = None,
        context_builder: ContextBuilder | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.provider = provider
        self.registry = registry
        self.session = session
        self.config = config or AgentConfig()
        self.context_builder = context_builder or ContextBuilder()
        self.on_event = on_event
        if self.config.max_steps < 1 or self.config.max_repeated_calls < 1 or self.config.max_verification_attempts < 1:
            raise ValueError("loop limits must be positive")
        original_observer = context.approval_observer

        def record_approval(tool_name: str, arguments: dict[str, Any], approved: bool) -> None:
            if original_observer:
                original_observer(tool_name, arguments, approved)
            self._record("approval", {"tool": tool_name, "arguments": self._bounded_arguments(arguments), "approved": approved})

        self.context = ToolContext(context.guard, context.approval, approval_observer=record_approval, mode=context.mode, secrets=context.secrets)

    def _record(self, kind: str, payload: dict[str, Any]) -> None:
        if self.session:
            try:
                self.session.append(kind, payload)
            except Exception as exc:  # audit I/O must not erase the task result
                if self.on_event and kind != "session_error":
                    self.on_event(
                        "session_error",
                        {
                            "event": kind,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
        if self.on_event:
            self.on_event(kind, payload)

    @staticmethod
    def _bounded_arguments(arguments: Any, limit: int = 4_000) -> Any:
        if isinstance(arguments, str):
            return arguments[:limit] + ("\n[argument truncated]" if len(arguments) > limit else "")
        if isinstance(arguments, dict):
            return {str(key): AgentLoop._bounded_arguments(value, limit) for key, value in arguments.items()}
        if isinstance(arguments, list):
            return [AgentLoop._bounded_arguments(value, limit) for value in arguments]
        return arguments

    async def run(self, prompt: str) -> LoopResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        messages: list[Message] = [
            self.context_builder.system_message(
                self.context.guard.root,
                tuple(definition.name for definition in self.registry.definitions(self.context.mode)),
                approval_mode="interactive or explicit auto-approve",
                mode=self.context.mode.value,
            ),
            Message(role="user", content=prompt),
        ]
        self._record("mode", {"mode": self.context.mode.value, "side_effects_allowed": self.context.mode is AgentMode.ACT})
        self._record("user_message", {"content": prompt, "mode": self.context.mode.value})
        seen_calls: dict[str, int] = {}
        verification_runs = 0
        verification_ok: bool | None = None
        explored: list[str] = []

        for step in range(self.config.max_steps):
            request_messages = self.context_builder.fit(messages)
            try:
                response = await self.provider.complete(request_messages, self.registry.schemas(self.context.mode))
            except (KeyboardInterrupt, asyncio.CancelledError):
                error_text = "agent interrupted by user"
                self._record("final", {"stopped_reason": "interrupted", "error": error_text})
                return LoopResult(tuple(messages), "interrupted", error_text, verification_ok, self.context.mode.value, explored=tuple(explored))
            except ProviderError as exc:
                error_text = str(exc)
                self._record("error", {"category": exc.category, "message": error_text})
                result = LoopResult(tuple(messages), "provider_error", error_text, verification_ok, self.context.mode.value, explored=tuple(explored))
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                self._record("error", {"category": "unexpected_provider_error", "message": error_text})
                result = LoopResult(tuple(messages), "provider_error", error_text, verification_ok, self.context.mode.value, explored=tuple(explored))
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result

            if not is_valid_response(response):
                error_text = "model returned an invalid response"
                self._record("error", {"category": "invalid_response", "message": error_text})
                result = LoopResult(tuple(messages), "invalid_response", error_text, verification_ok, self.context.mode.value, explored=tuple(explored))
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result

            if not response.message.content and not response.message.tool_calls:
                error_text = "model returned an empty response"
                self._record("error", {"category": "empty_response", "message": error_text})
                result = LoopResult(tuple(messages), "empty_response", error_text, verification_ok, self.context.mode.value, explored=tuple(explored))
                self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text})
                return result

            messages.append(response.message)
            self._record("model_message", {"step": step, "content": response.message.content[:16_000], "tool_calls": [call.name for call in response.message.tool_calls], "finish_reason": response.finish_reason})
            if not response.message.tool_calls:
                if self.context.mode is AgentMode.PLAN:
                    plan_summary = response.message.content
                    self._record("plan_summary", {"content": plan_summary[:16_000], "explored": explored})
                    if self.config.verification_command:
                        self._record("verification_skipped", {"reason": "plan_mode", "command": self._bounded_arguments(self.config.verification_command)})
                    result = LoopResult(
                        tuple(messages),
                        "model_finished",
                        verification_ok=None,
                        mode=self.context.mode.value,
                        plan_summary=plan_summary,
                        explored=tuple(explored),
                    )
                    self._record("final", {"stopped_reason": result.stopped_reason, "mode": self.context.mode.value, "verification_ok": None})
                    return result
                if self.config.verification_command and verification_ok is not True and verification_runs < self.config.max_verification_attempts:
                    verification_runs += 1
                    verification_arguments = {"command": self.config.verification_command, "timeout_seconds": 120}
                    self._record("verification_start", {"attempt": verification_runs, "command": self._bounded_arguments(self.config.verification_command)})
                    verification = self.registry.execute("run_command", verification_arguments, self.context)
                    verification_ok = verification.ok
                    self._record("verification_result", {"attempt": verification_runs, "ok": verification.ok, "output": verification.output[:20_000], "metadata": self._bounded_arguments(verification.metadata)})
                    messages.append(Message(role="user", content=f"Verification result for `{self.config.verification_command}`:\n{verification.output}"))
                    if not verification.ok and verification_runs >= self.config.max_verification_attempts:
                        error_text = "verification command failed after the configured attempts"
                        result = LoopResult(tuple(messages), "verification_failed", error_text, False, self.context.mode.value, explored=tuple(explored))
                        self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text, "verification_ok": False})
                        return result
                    continue
                result = LoopResult(tuple(messages), "model_finished", verification_ok=verification_ok, mode=self.context.mode.value, explored=tuple(explored))
                self._record("final", {"stopped_reason": result.stopped_reason, "verification_ok": verification_ok})
                return result

            for call in response.message.tool_calls:
                fingerprint_source = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, default=str)
                fingerprint = hashlib.sha256(f"{call.name}:{fingerprint_source}".encode("utf-8", errors="replace")).hexdigest()
                seen_calls[fingerprint] = seen_calls.get(fingerprint, 0) + 1
                self._record("tool_call", {"step": step, "id": call.id, "tool": call.name, "arguments": self._bounded_arguments(call.arguments)})
                if seen_calls[fingerprint] > self.config.max_repeated_calls:
                    output = "repeated identical tool call limit exceeded"
                    self._record("error", {"category": "repeated_tool_call", "message": output, "tool": call.name})
                    messages.append(Message(role="tool", content=output, tool_call_id=call.id))
                    result = LoopResult(tuple(messages), "repeated_tool_call", output, verification_ok, self.context.mode.value, explored=tuple(explored))
                    self._record("final", {"stopped_reason": result.stopped_reason, "error": output})
                    return result
                tool_result = self.registry.execute(call.name, call.arguments, self.context)
                if call.name in {"list_files", "read_file", "search", "workspace_summary"}:
                    detail = str(tool_result.metadata.get("path") or call.arguments.get("path") or call.arguments.get("pattern") or call.name)
                    explored.append(f"{call.name}:{detail}"[:500])
                if tool_result.metadata.get("error") == "mode_denied":
                    self._record(
                        "mode_denied",
                        {
                            "step": step,
                            "id": call.id,
                            "tool": call.name,
                            "mode": self.context.mode.value,
                            "reason": tool_result.output[:1_000],
                        },
                    )
                self._record("tool_result", {"step": step, "id": call.id, "tool": call.name, "ok": tool_result.ok, "output": tool_result.output[:20_000], "metadata": self._bounded_arguments(tool_result.metadata)})
                messages.append(Message(role="tool", content=self._tool_message_content(tool_result), tool_call_id=call.id))

        error_text = f"maximum agent steps reached ({self.config.max_steps})"
        result = LoopResult(tuple(messages), "max_steps", error_text, verification_ok, self.context.mode.value, explored=tuple(explored))
        self._record("final", {"stopped_reason": result.stopped_reason, "error": error_text, "verification_ok": verification_ok})
        return result

    @staticmethod
    def _tool_message_content(tool_result) -> str:
        metadata = json.dumps(tool_result.metadata, ensure_ascii=False, sort_keys=True, default=str)
        return f"{tool_result.output}\n[metadata] {metadata}"
