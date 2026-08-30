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


def test_agent_loop_labels_first_progress_as_initial_analysis(tmp_path: Path):
    events = []
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(
        FakeProvider(),
        build_default_registry(guard),
        ToolContext(guard, AllowAllApproval()),
        on_event=lambda kind, payload: events.append((kind, payload)),
    )
    asyncio.run(loop.run("create an answer file"))
    progress = [payload for kind, payload in events if kind == "model_progress"]
    assert progress[0]["step"] == 1
    assert progress[0]["message"].startswith("Analyzing the task")
    requests = [payload for kind, payload in events if kind == "model_request"]
    messages = [payload for kind, payload in events if kind == "model_message"]
    assert requests and messages
    assert requests[0]["turn_id"] == progress[0]["turn_id"] == messages[0]["turn_id"]


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


def test_agent_loop_fails_fast_on_required_stream_capability_mismatch(tmp_path: Path):
    class NoStreamProvider(FakeProvider):
        stream_required = True
        @property
        def capabilities(self):
            return ModelCapabilities(streaming=False)

    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(NoStreamProvider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval()))
    result = asyncio.run(loop.run("hello"))
    assert result.stopped_reason == "capability_mismatch"
    assert result.error == "configured provider requires streaming but does not support it"
