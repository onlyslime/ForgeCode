import asyncio
import time
from pathlib import Path

from forgecode.agent import AgentConfig, AgentLoop
from forgecode.models import CancellationToken, Message, ModelResponse, ToolCall
from forgecode.security import WorkspaceGuard
from forgecode.storage import SessionStore
from forgecode.tools import AgentMode, AllowAllApproval, DenyAllApproval, ToolContext, build_default_registry
from forgecode.tools.base import ToolDefinition, ToolRegistry, ToolResult
from forgecode.tools.understanding import FindDefinitionTool, FindReferencesTool


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


def test_read_only_tool_batch_runs_concurrently_and_keeps_result_order(tmp_path):
    class SlowRead:
        definition = ToolDefinition("read_file", "slow read", {"type": "object"})

        def execute(self, arguments, context):
            time.sleep(0.08)
            return ToolResult(True, str(arguments["value"]), {"value": arguments["value"]})

    class Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(Message("assistant", tool_calls=(
                    ToolCall("a", "read_file", {"value": "first"}),
                    ToolCall("b", "read_file", {"value": "second"}),
                )))
            return ModelResponse(Message("assistant", "done"))

    registry = ToolRegistry()
    registry.register(SlowRead())
    events = []
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(Provider(), registry, ToolContext(guard), on_event=lambda k, p: events.append((k, p)))
    started = time.monotonic()
    result = asyncio.run(loop.run("read both"))
    elapsed = time.monotonic() - started
    assert result.stopped_reason == "model_finished"
    # Two 80ms calls should overlap; allow CI/Windows scheduling overhead
    # while still rejecting the ~160ms serial path.
    assert elapsed < 0.25
    assert [m.tool_call_id for m in result.messages if m.role == "tool"] == ["a", "b"]
    assert any(kind == "tool_batch_parallel" for kind, _ in events)


def test_new_read_only_tools_are_eligible_for_parallel_batches(tmp_path):
    class FastRead:
        definition = ToolDefinition("symbol_hover", "static hover", {"type": "object"})
        def execute(self, arguments, context):
            return ToolResult(True, "ok", {})
    class Provider:
        def __init__(self): self.calls = 0
        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(Message("assistant", tool_calls=(ToolCall("a", "symbol_hover", {}), ToolCall("b", "symbol_hover", {}))))
            return ModelResponse(Message("assistant", "done"))
    registry = ToolRegistry(); registry.register(FastRead())
    events = []; guard = WorkspaceGuard(tmp_path)
    result = asyncio.run(AgentLoop(Provider(), registry, ToolContext(guard), on_event=lambda k,p: events.append((k,p))).run("hover"))
    assert result.stopped_reason == "model_finished"
    assert any(kind == "tool_batch_parallel" for kind, _ in events)


def test_read_only_batch_cancellation_marks_queued_calls(tmp_path):
    token = CancellationToken()

    class CancelRead:
        definition = ToolDefinition("read_file", "cancellable read", {"type": "object"})

        def execute(self, arguments, context):
            time.sleep(0.03 if arguments["value"] == 0 else 0.2)
            if arguments["value"] == 0:
                token.cancel("test cancellation")
            return ToolResult(True, str(arguments["value"]), {})

    class Provider:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(Message("assistant", tool_calls=tuple(
                    ToolCall(str(i), "read_file", {"value": i}) for i in range(6)
                )))
            return ModelResponse(Message("assistant", "done"))

    registry = ToolRegistry()
    registry.register(CancelRead())
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(Provider(), registry, ToolContext(guard, cancellation_token=token))
    result = asyncio.run(loop.run("read many"))
    tool_messages = [m for m in result.messages if m.role == "tool"]
    assert len(tool_messages) == 6
    assert any("cancelled before execution" in m.content for m in tool_messages)


def test_read_only_batch_with_hooks_remains_serial(tmp_path):
    active = 0; maximum = 0
    class HookedRead:
        definition = ToolDefinition("read_file", "hooked read", {"type": "object"})
        def execute(self, arguments, context):
            nonlocal active, maximum
            active += 1; maximum = max(maximum, active)
            time.sleep(0.01)
            active -= 1
            return ToolResult(True, "ok", {})
    class Hooks:
        def emit(self, *args, **kwargs): return ()
    class Provider:
        def __init__(self): self.calls = 0
        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(Message("assistant", tool_calls=(ToolCall("a", "read_file", {"value": 1}), ToolCall("b", "read_file", {"value": 2}))))
            return ModelResponse(Message("assistant", "done"))
    registry = ToolRegistry(); registry.register(HookedRead())
    context = ToolContext(WorkspaceGuard(tmp_path), hooks=Hooks())
    result = asyncio.run(AgentLoop(Provider(), registry, context).run("read"))
    assert result.stopped_reason == "model_finished" and maximum == 1


def test_semantic_navigation_tools_are_bounded_and_guarded(tmp_path):
    (tmp_path / "mod.py").write_text("def target():\n    return target()\n", encoding="utf-8")
    context = ToolContext(WorkspaceGuard(tmp_path))
    definition = FindDefinitionTool().execute({"symbol": "target"}, context)
    references = FindReferencesTool().execute({"symbol": "target"}, context)
    assert definition.metadata["matches"][0]["line"] == 1
    assert references.metadata["count"] == 2


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
    assert approvals[0].payload["decision"] == "deny"
    assert approvals[0].payload["scope"] in {"changes", "execution", "evidence", "other"}


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


def test_session_write_failure_does_not_discard_agent_result(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", "done"))])
    # A directory is intentionally supplied where JSONL should be a file.
    broken_session = tmp_path / "session-dir"
    broken_session.mkdir()
    loop = _loop(tmp_path, provider, AllowAllApproval(), session=SessionStore(broken_session))
    result = asyncio.run(loop.run("x"))
    assert result.succeeded


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


def test_context_budget_bounds_nested_arguments_and_many_tool_calls(tmp_path):
    calls = tuple(
        ToolCall(str(index), "search", {"items": list(range(1_000)), "query": f"x{index}" + "y" * 20_000})
        for index in range(200)
    )
    provider = ScriptedProvider([
        ModelResponse(Message("assistant", tool_calls=calls)),
        ModelResponse(Message("assistant", "done")),
    ])
    loop = _loop(tmp_path, provider, AllowAllApproval())
    loop.context_builder = loop.context_builder.__class__(max_chars=2_000, max_message_chars=400)
    result = asyncio.run(loop.run("x"))
    assert result.succeeded, result.stopped_reason
    from forgecode.agent.context import _message_size

    assert sum(_message_size(message) for message in provider.requests[1][0]) <= 2_000


def test_context_fit_keeps_tool_call_exchange_atomic():
    from forgecode.agent.context import ContextBuilder

    messages = [
        Message("system", "system"),
        Message("user", "request"),
        Message("assistant", tool_calls=(ToolCall("a", "read_file", {"path": "a"}), ToolCall("b", "read_file", {"path": "b"}))),
        Message("tool", "result a", tool_call_id="a"),
        Message("tool", "result b", tool_call_id="b"),
        Message("user", "next"),
    ]
    fitted = ContextBuilder(max_chars=80, max_message_chars=40).fit(messages)
    calls = {call.id for message in fitted if message.role == "assistant" for call in message.tool_calls}
    results = {message.tool_call_id for message in fitted if message.role == "tool"}
    assert calls == results
    assert all(message.role != "tool" or message.tool_call_id in calls for message in fitted)


def test_system_prompt_requires_conversational_progress_updates(tmp_path):
    from forgecode.agent.context import ContextBuilder

    prompt = ContextBuilder().system_message(tmp_path, ["read_file"], approval_mode="auto", mode="act").content
    assert "three-phase workflow" in prompt
    assert "Do not remain silent" in prompt


def test_verification_failure_is_visible_without_invalid_tool_message(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", "done")), ModelResponse(Message("assistant", "still done"))])
    loop = _loop(tmp_path, provider, AllowAllApproval(), config=AgentConfig(verification_command="python -c \"import sys; sys.exit(2)\"", max_verification_attempts=1))
    result = asyncio.run(loop.run("x"))
    assert result.stopped_reason == "verification_failed"
    assert all(message.role != "tool" or message.tool_call_id is not None for message in result.messages)


def test_plan_mode_blocks_side_effects_and_filters_schemas(tmp_path):
    provider = ScriptedProvider([
        ModelResponse(Message("assistant", tool_calls=(ToolCall("write", "write_file", {"path": "blocked.txt", "content": "no"}),))),
        ModelResponse(Message("assistant", "Plan: inspect first, then edit in act mode.")),
    ])
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(provider, build_default_registry(guard), ToolContext(guard, AllowAllApproval(), mode=AgentMode.PLAN))
    result = asyncio.run(loop.run("plan a fix"))
    assert result.mode == "plan"
    assert result.plan_summary and "act mode" in result.plan_summary
    assert not (tmp_path / "blocked.txt").exists()
    assert all(schema["function"]["name"] not in {"write_file", "apply_patch", "run_command"} for schema in provider.requests[0][1])
    blocked = next(message for message in provider.requests[1][0] if message.role == "tool")
    assert "plan mode" in blocked.content


def test_plan_mode_never_runs_verification(tmp_path):
    provider = ScriptedProvider([ModelResponse(Message("assistant", "plan only"))])
    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / "plan.jsonl")
    loop = AgentLoop(provider, build_default_registry(guard), ToolContext(guard, AllowAllApproval(), mode="plan"), session=session, config=AgentConfig(verification_command="python -c \"raise SystemExit(9)\""))
    result = asyncio.run(loop.run("plan"))
    assert result.succeeded
    assert result.verification_ok is None
    events = list(session.read())
    assert any(event.kind == "verification_skipped" and event.payload["reason"] == "plan_mode" for event in events)


def test_plan_mode_records_mode_denial_event(tmp_path):
    provider = ScriptedProvider([
        ModelResponse(Message("assistant", tool_calls=(ToolCall("blocked", "write_file", {"path": "x.txt", "content": "no"}),))),
        ModelResponse(Message("assistant", "plan only")),
    ])
    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / "plan.jsonl")
    loop = AgentLoop(provider, build_default_registry(guard), ToolContext(guard, AllowAllApproval(), mode="plan"), session=session)
    asyncio.run(loop.run("plan"))
    events = list(session.read())
    denied = [event for event in events if event.kind == "mode_denied"]
    assert denied and denied[0].payload["tool"] == "write_file"
def test_tool_message_content_rejects_non_json_metadata():
    result = ToolResult(True, "ok", {"bad": object()})
    content = AgentLoop._tool_message_content(result)
    assert "metadata_not_json_safe" in content


def test_record_isolates_failing_event_callback():
    loop = AgentLoop.__new__(AgentLoop)
    loop.session = None
    loop.audit_complete = True
    seen = []
    def callback(kind, payload):
        seen.append(kind)
        if kind == "tool_result":
            raise RuntimeError("ui failed")
    loop.on_event = callback
    loop._record("tool_result", {"ok": True})
    assert loop.audit_complete is False
    assert seen == ["tool_result", "session_error"]
