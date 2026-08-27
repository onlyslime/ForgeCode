"""The provider-neutral model -> tool -> result loop."""

from dataclasses import dataclass

from ..models import Message, ModelProvider
from ..storage import SessionStore
from ..tools import ToolContext, ToolRegistry


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 12


@dataclass(frozen=True)
class LoopResult:
    messages: tuple[Message, ...]
    stopped_reason: str


class AgentLoop:
    def __init__(self, provider: ModelProvider, registry: ToolRegistry, context: ToolContext, session: SessionStore | None = None, config: AgentConfig | None = None):
        self.provider = provider
        self.registry = registry
        self.context = context
        self.session = session
        self.config = config or AgentConfig()
        if self.config.max_steps < 1:
            raise ValueError("max_steps must be positive")

    async def run(self, prompt: str) -> LoopResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        messages: list[Message] = [Message(role="user", content=prompt)]
        if self.session:
            self.session.append("user_message", {"content": prompt})

        for step in range(self.config.max_steps):
            response = await self.provider.complete(messages, self.registry.schemas())
            messages.append(response.message)
            if self.session:
                self.session.append("model_message", {"step": step, "content": response.message.content, "tool_calls": [call.name for call in response.message.tool_calls]})
            if not response.message.tool_calls:
                return LoopResult(tuple(messages), "model_finished")

            for call in response.message.tool_calls:
                result = self.registry.execute(call.name, call.arguments, self.context)
                messages.append(Message(role="tool", content=result.output, tool_call_id=call.id))
                if self.session:
                    self.session.append("tool_result", {"step": step, "tool": call.name, "ok": result.ok, "output": result.output, "metadata": result.metadata})
        return LoopResult(tuple(messages), "max_steps")
