import asyncio
from pathlib import Path
import threading

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


def test_agent_loop_status_snapshot_is_bounded_and_tracks_run(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(FakeProvider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval()))
    before = loop.status_snapshot()
    assert before["active"] is False
    asyncio.run(loop.run("create an answer file"))
    after = loop.status_snapshot()
    assert after["active"] is False
    assert after["state"] == "completed"
    assert after["step"] == 2
    assert after["provider_requests"] == 2
    assert after["tool_calls"] == 1
    assert after["run_id"] is None
    assert set(after) == {"active", "state", "run_id", "step", "provider_requests", "tool_calls", "elapsed_seconds", "remaining_seconds", "steering_items", "steering_chars", "cancelled", "audit_complete"}

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


def test_agent_loop_steering_is_injected_at_next_model_boundary(tmp_path: Path):
    started = threading.Event()
    release = threading.Event()
    observed = []

    class Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, _tools):
            self.calls += 1
            observed.append([message.content for message in messages if message.role == "user"])
            if self.calls == 1:
                started.set()
                while not release.is_set():
                    await asyncio.sleep(0.005)
                return ModelResponse(Message("assistant", tool_calls=(ToolCall("inspect", "list_files", {"pattern": "*"}),)), finish_reason="tool_calls")
            return ModelResponse(Message("assistant", "done"), finish_reason="stop")

    provider = Provider()
    loop = AgentLoop(provider, build_default_registry(WorkspaceGuard(tmp_path)), ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval()))

    async def scenario():
        task = asyncio.create_task(loop.run("initial task"))
        while not started.is_set():
            await asyncio.sleep(0.005)
        assert loop.steer("focus on the failing edge case")["accepted"] is True
        release.set()
        return await task

    result = asyncio.run(scenario())
    assert result.succeeded
    assert any("focus on the failing edge case" in item for item in observed[-1])


def test_agent_loop_cancel_clears_pending_steering(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(object(), build_default_registry(guard), ToolContext(guard, AllowAllApproval()))
    assert loop.steer("do not run after cancellation")["accepted"] is True
    assert loop.cancel("user cancelled") is True
    assert not loop._steering_queue
