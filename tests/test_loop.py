import asyncio
from pathlib import Path

from forgecode.agent import AgentLoop
from forgecode.models import Message, ModelCapabilities, ModelResponse, ToolCall
from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry


class FakeProvider:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("1", "write_file", {"path": "answer.txt", "content": "done"}),)))
        return ModelResponse(Message("assistant", "finished"))


def test_agent_loop_executes_tool_then_stops(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(FakeProvider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval()))
    result = asyncio.run(loop.run("create an answer file"))
    assert result.stopped_reason == "model_finished"
    assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "done"


def test_agent_loop_fails_fast_on_explicit_provider_tool_capability_mismatch(tmp_path: Path):
    class NoToolsProvider(FakeProvider):
        @property
        def capabilities(self):
            return ModelCapabilities(tool_calling=False)

    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(NoToolsProvider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval()))
    result = asyncio.run(loop.run("create an answer file"))
    assert result.stopped_reason == "capability_mismatch"
    assert result.error == "configured provider does not support tool calling"
