from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time

from forgecode.agent import AgentConfig, AgentLoop
from forgecode.application import InteractiveRunController
from forgecode.application.commands import main
from forgecode.models import Message, ModelResponse
from forgecode.security.workspace import WorkspaceGuard
from forgecode.storage import SessionStore
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry


def test_controller_fifo_queue_is_bounded_and_cancel_drops_followups():
    started = threading.Event()
    release = threading.Event()
    seen: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    def start(message: str) -> dict[str, object]:
        seen.append(message)
        started.set()
        release.wait(2)
        return {"message": message}

    controller = InteractiveRunController(start, event_sink=lambda kind, payload: events.append((kind, payload)), max_queue_items=1, max_queue_chars=32)
    assert controller.submit("first")["accepted"] is True
    assert controller.submit("second")["queued"] is True
    rejected = controller.submit("third")
    assert rejected["accepted"] is False and "full" in str(rejected["error"])
    assert controller.cancel()["cancelled"] is True
    release.set()
    assert controller.join(2)
    assert seen == ["first"]
    assert any(kind == "followup_rejected" for kind, _ in events)


def test_controller_snapshot_retains_last_run_metrics_after_completion():
    controller = InteractiveRunController(lambda _message: {"message": "ok"})
    assert controller.submit("one")["accepted"] is True
    assert controller.join(2)
    snapshot = controller.snapshot()
    assert snapshot["active"] is False
    assert isinstance(snapshot["last_elapsed_seconds"], float)
    assert snapshot["last_tool_steps"] == 0


def test_agent_loop_pause_after_provider_return_then_resume(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / "run.jsonl", run_id="pause-run", mode="act")
    provider_started = threading.Event()
    release = threading.Event()
    class Provider:
        async def complete(self, _messages, _tools, context=None):
            provider_started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return ModelResponse(Message("assistant", "done"), finish_reason="stop")

    loop = AgentLoop(
        Provider(),
        build_default_registry(guard),
        ToolContext(guard, AllowAllApproval(), run_id="pause-run"),
        session=session,
        config=AgentConfig(max_steps=1),
    )
    loop.enable_interactive_controls()

    async def scenario():
        task = asyncio.create_task(loop.run("wait"))
        while not provider_started.is_set():
            await asyncio.sleep(0.005)
        loop.pause()
        release.set()
        for _ in range(200):
            if loop.lifecycle.state.value == "paused":
                break
            await asyncio.sleep(0.005)
        assert loop.lifecycle.state.value == "paused"
        loop.resume()
        return await asyncio.wait_for(task, timeout=2)

    result = asyncio.run(scenario())
    assert result.succeeded and result.state == "completed"
    kinds = [event.kind for event in session.read(strict=True)]
    assert "pause" in kinds and "resume" in kinds


def test_pause_racing_approval_blocks_side_effect_until_resume(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / "run.jsonl", run_id="approval-pause", mode="act")
    approved = threading.Event()
    release = threading.Event()

    class Approval:
        def approve(self, _tool, _arguments):
            approved.set()
            time.sleep(0.05)
            return True

    class Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, _messages, _tools, context=None):
            self.calls += 1
            if self.calls == 1:
                from forgecode.models import ToolCall

                return ModelResponse(Message("assistant", tool_calls=(ToolCall("w", "write_file", {"path": "result.txt", "content": "done"}),)), finish_reason="tool_calls")
            return ModelResponse(Message("assistant", "done"), finish_reason="stop")

    loop = AgentLoop(
        Provider(),
        build_default_registry(guard),
        ToolContext(guard, Approval(), run_id="approval-pause"),
        session=session,
        config=AgentConfig(max_steps=2),
    )
    loop.enable_interactive_controls()
    def pause_then_resume():
        approved.wait(1)
        loop.pause()
        time.sleep(0.1)
        loop.resume()

    control_thread = threading.Thread(target=pause_then_resume, daemon=True)
    control_thread.start()

    async def scenario():
        task = asyncio.create_task(loop.run("write"))
        return await asyncio.wait_for(task, timeout=2)

    result = asyncio.run(scenario())
    control_thread.join(1)
    assert result.succeeded and (tmp_path / "result.txt").read_text(encoding="utf-8") == "done"


def test_chat_active_model_select_is_rejected_without_provider_change(capsys, monkeypatch, tmp_path: Path):
    started = threading.Event()

    class SlowProvider:
        def __init__(self, **_kwargs):
            pass

        async def complete(self, _messages, _tools, context=None):
            started.set()
            while context is None or not context.cancelled:
                await asyncio.sleep(0.005)
            from forgecode.models import ProviderError

            raise ProviderError("cancelled", category="cancelled", retryable=False)

    monkeypatch.setattr("forgecode.application.commands.OpenAICompatibleProvider", SlowProvider)
    monkeypatch.setattr("sys.stdin", iter(["start work", "/model select default", "/quit"]))
    code = main(["--workspace", str(tmp_path), "chat", "--jsonl"])
    assert code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    rejected = [record for record in records if record.get("data", {}).get("code") == "worker_active" or record.get("payload", {}).get("code") == "worker_active"]
    assert rejected
    session_path = next((tmp_path / ".forgecode" / "sessions").glob("*.jsonl"))
    assert any(event.kind == "profile_switch_rejected" for event in SessionStore(session_path).read(strict=True))
