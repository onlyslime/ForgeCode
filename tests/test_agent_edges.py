import asyncio
from pathlib import Path

from forgecode.agent import AgentConfig, AgentLoop
from forgecode.models import Message, ModelResponse, ToolCall
from forgecode.security import WorkspaceGuard
from forgecode.storage import SessionStore
from forgecode.tools import AllowAllApproval, DenyAllApproval, ToolContext, build_default_registry


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append((list(messages), list(tools)))
        return self.responses.pop(0)


def _loop(tmp_path: Path, provider, approval, *, config=None, session=None):
    guard = WorkspaceGuard(tmp_path)
    return AgentLoop(provider, build_default_registry(guard), ToolContext(guard, approval), session=session, config=config)


def test_multiple_tool_calls_preserve_ids_and_results(tmp_path):
    provider = ScriptedProvider([
        ModelResponse(Message("assistant", tool_calls=(ToolCall("one", "write_file", {"path": "a.txt", "content": "a"}), ToolCall("two", "write_file", {"path": "b.txt", "content": "b"})))),
        ModelResponse(Message("assistant", "done")),
    ])
    result = asyncio.run(_loop(tmp_path, provider, AllowAllApproval()).run("create two files"))
    assert result.succeeded
    assert (tmp_path / "a.txt").read_text() == "a"
    assert (tmp_path / "b.txt").read_text() == "b"
    tool_messages = [message for message in provider.requests[1][0] if message.role == "tool"]
    assert [message.tool_call_id for message in tool_messages] == ["one", "two"]


def test_unknown_tool_is_returned_to_model(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", tool_calls=(ToolCall("x", "missing", {}),))), ModelResponse(Message("assistant", "recover"))])
    result = asyncio.run(_loop(tmp_path, provider, AllowAllApproval()).run("try"))
    assert result.stopped_reason == "model_finished"
    assert "unknown tool: missing" in next(message.content for message in provider.requests[1][0] if message.role == "tool")


def test_denied_write_does_not_modify_workspace(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", tool_calls=(ToolCall("x", "write_file", {"path": "blocked.txt", "content": "no"}),))), ModelResponse(Message("assistant", "stopped"))])
    result = asyncio.run(_loop(tmp_path, provider, DenyAllApproval()).run("write"))
    assert result.succeeded
    assert not (tmp_path / "blocked.txt").exists()
    assert "denied" in next(message.content for message in provider.requests[1][0] if message.role == "tool")


def test_approval_decision_is_recorded_in_session(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", tool_calls=(ToolCall("x", "write_file", {"path": "blocked.txt", "content": "no"}),))), ModelResponse(Message("assistant", "stopped"))])
    session = SessionStore(tmp_path / "run.jsonl")
    loop = _loop(tmp_path, provider, DenyAllApproval(), session=session)
    asyncio.run(loop.run("write"))
    events = list(session.read())
    approvals = [event for event in events if event.kind == "approval"]
    assert approvals and approvals[0].payload["approved"] is False


def test_empty_response_and_max_steps_have_clear_stop_reasons(tmp_path):
    empty = ScriptedProvider([ModelResponse(Message("assistant", ""))])
    assert asyncio.run(_loop(tmp_path, empty, AllowAllApproval()).run("x")).stopped_reason == "empty_response"
    looping = ScriptedProvider([ModelResponse(Message("assistant", tool_calls=(ToolCall("x", "list_files", {"pattern": "*"}),)))])
    result = asyncio.run(_loop(tmp_path, looping, AllowAllApproval(), config=AgentConfig(max_steps=1)).run("x"))
    assert result.stopped_reason == "max_steps"


def test_invalid_provider_response_has_typed_stop_reason(tmp_path):
    provider = ScriptedProvider([None])
    result = asyncio.run(_loop(tmp_path, provider, AllowAllApproval()).run("x"))
    assert result.stopped_reason == "invalid_response"


def test_malformed_provider_message_has_typed_stop_reason(tmp_path):
    malformed = ModelResponse(Message("assistant", "ok"))
    object.__setattr__(malformed.message, "content", None)
    provider = ScriptedProvider([malformed])
    result = asyncio.run(_loop(tmp_path, provider, AllowAllApproval()).run("x"))
    assert result.stopped_reason == "invalid_response"


def test_repeated_call_is_stopped(tmp_path):
    call = ModelResponse(Message("assistant", tool_calls=(ToolCall("x", "list_files", {"pattern": "*"}),)))
    provider = ScriptedProvider([call, call, call])
    result = asyncio.run(_loop(tmp_path, provider, AllowAllApproval(), config=AgentConfig(max_steps=5, max_repeated_calls=2)).run("x"))
    assert result.stopped_reason == "repeated_tool_call"


def test_failed_command_is_visible_and_can_be_repaired(tmp_path):
    provider = ScriptedProvider([
        ModelResponse(Message("assistant", tool_calls=(ToolCall("bad", "run_command", {"command": "python -c \"import sys; sys.exit(3)\""}),))),
        ModelResponse(Message("assistant", tool_calls=(ToolCall("good", "run_command", {"command": "python -c \"print('fixed')\""}),))),
        ModelResponse(Message("assistant", "fixed")),
    ])
    result = asyncio.run(_loop(tmp_path, provider, AllowAllApproval()).run("run and repair"))
    assert result.succeeded
    assert "exit_code" in next(message.content for message in provider.requests[1][0] if message.role == "tool")


def test_session_records_and_redacts_secret(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", "done"))])
    session = SessionStore(tmp_path / "run.jsonl", secrets=["secret-value"])
    loop = _loop(tmp_path, provider, AllowAllApproval(), session=session)
    asyncio.run(loop.run("secret-value"))
    raw = (tmp_path / "run.jsonl").read_text()
    assert "secret-value" not in raw
    assert "REDACTED" in raw


def test_context_budget_keeps_request_bounded(tmp_path):
    provider = ScriptedProvider([
        ModelResponse(Message("assistant", tool_calls=(ToolCall("x", "write_file", {"path": "a.txt", "content": "z" * 10_000}),))),
        ModelResponse(Message("assistant", "done")),
    ])
    loop = _loop(tmp_path, provider, AllowAllApproval())
    loop.context_builder = loop.context_builder.__class__(max_chars=1_000, max_message_chars=500)
    result = asyncio.run(loop.run("x"))
    assert result.succeeded
    from forgecode.agent.context import _message_size

    assert sum(_message_size(message) for message in provider.requests[1][0]) <= 1_000


def test_verification_failure_is_visible_without_invalid_tool_message(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", "done")), ModelResponse(Message("assistant", "still done"))])
    loop = _loop(tmp_path, provider, AllowAllApproval(), config=AgentConfig(verification_command="python -c \"import sys; sys.exit(2)\"", max_verification_attempts=1))
    result = asyncio.run(loop.run("x"))
    assert result.stopped_reason == "verification_failed"
    assert all(message.role != "tool" or message.tool_call_id is not None for message in result.messages)
