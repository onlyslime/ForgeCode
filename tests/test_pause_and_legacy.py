import asyncio
import json
from pathlib import Path

from forgecode.agent import AgentLoop, RunState
from forgecode.models import Message, ModelResponse
from forgecode.security import WorkspaceGuard
from forgecode.storage import SessionStore
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry


def test_legacy_session_events_are_read_as_schema_zero(tmp_path: Path):
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps({"kind": "user_message", "payload": {"content": "old"}, "timestamp": "2026-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
    event = next(SessionStore(path).read())
    assert event.schema_version == 0 and event.sequence == 0


def test_loop_cooperative_pause_has_checkpoint_and_typed_state(tmp_path: Path):
    class Provider:
        async def complete(self, messages, tools):
            return ModelResponse(Message("assistant", "finished"))

    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / "run.jsonl")
    loop = AgentLoop(Provider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval()), session=session)
    loop.pause()
    result = asyncio.run(loop.run("pause"))
    assert result.stopped_reason == "paused" and result.state == RunState.PAUSED.value
    assert (tmp_path / "run.checkpoint.json").is_file()
