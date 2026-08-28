"""Regression tests for cancellation, deadlines and recovery boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import threading
import time

import pytest

from forgecode.agent import AgentConfig, AgentLoop
from forgecode.models import CancellationToken, Message, ModelResponse, OpenAICompatibleProvider, ProviderContext, ProviderError, ToolCall
from forgecode.security.workspace import WorkspaceGuard
from forgecode.storage import SessionStore, TransactionStore
from forgecode.testing import TestProfile as _TestProfile, TestProfileRunner as _TestProfileRunner
from forgecode.tools import AllowAllApproval, ToolContext, ToolDefinition, ToolRegistry, ToolResult, build_default_registry


class _CancelOnApproval:
    def __init__(self, token: CancellationToken):
        self.token = token

    def approve(self, _tool_name: str, _arguments: dict):
        self.token.cancel("cancelled during approval")
        return True


def test_write_file_cancellation_after_approval_does_not_mutate(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    token = CancellationToken()
    guard = WorkspaceGuard(tmp_path)
    result = build_default_registry(guard).execute(
        "write_file",
        {"path": "a.txt", "content": "after"},
        ToolContext(guard, _CancelOnApproval(token), cancellation_token=token, transaction_store=TransactionStore(guard), run_id="cancel-write"),
    )
    assert not result.ok
    assert result.metadata.get("error") == "cancelled"
    assert target.read_text(encoding="utf-8") == "before"
    assert not list((tmp_path / ".forgecode" / "transactions" / "manifests").glob("*.json"))


def test_apply_patch_cancellation_after_approval_does_not_mutate(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("before\n", encoding="utf-8")
    token = CancellationToken()
    guard = WorkspaceGuard(tmp_path)
    patch = """--- a/a.txt
+++ b/a.txt
@@ -1,1 +1,1 @@
-before
+after
"""
    result = build_default_registry(guard).execute(
        "apply_patch",
        {"patch": patch},
        ToolContext(guard, _CancelOnApproval(token), cancellation_token=token, transaction_store=TransactionStore(guard), run_id="cancel-patch"),
    )
    assert not result.ok
    assert result.metadata.get("error") == "cancelled"
    assert target.read_text(encoding="utf-8") == "before\n"
    assert not list((tmp_path / ".forgecode" / "transactions" / "manifests").glob("*.json"))


def test_shell_cancellation_after_approval_does_not_spawn_process(tmp_path: Path):
    token = CancellationToken()
    guard = WorkspaceGuard(tmp_path)
    result = build_default_registry(guard).execute(
        "run_command",
        {"command": f"{sys.executable} -c \"from pathlib import Path; Path('spawned').write_text('bad')\""},
        ToolContext(guard, _CancelOnApproval(token), cancellation_token=token),
    )
    assert not result.ok
    assert result.metadata.get("error") == "cancelled"
    assert not (tmp_path / "spawned").exists()


class _CancelingProvider:
    def __init__(self, token: CancellationToken):
        self.token = token

    async def complete(self, _messages, _tools, context=None):
        assert context is not None
        self.token.cancel("provider returned after cancellation request")
        return ModelResponse(Message("assistant", tool_calls=(ToolCall("write-1", "write_file", {"path": "a.txt", "content": "after"}),)), finish_reason="tool_calls")


def test_agent_loop_cancellation_after_provider_response_skips_tool(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    token = CancellationToken()
    loop = AgentLoop(
        _CancelingProvider(token),
        build_default_registry(guard),
        ToolContext(guard, AllowAllApproval(), cancellation_token=token, transaction_store=TransactionStore(guard), run_id="loop-cancel"),
        config=AgentConfig(max_steps=2),
        cancellation_token=token,
    )
    result = asyncio.run(loop.run("write a file"))
    assert result.stopped_reason == "cancelled"
    assert target.read_text(encoding="utf-8") == "before"
    assert not list((tmp_path / ".forgecode" / "transactions" / "manifests").glob("*.json"))


def test_agent_loop_cancel_interrupts_legacy_provider_without_dispatch(tmp_path: Path):
    """A legacy two-argument provider cannot strand a cancelled run."""

    class HangingProvider:
        async def complete(self, _messages, _tools):
            await asyncio.sleep(30)

    guard = WorkspaceGuard(tmp_path)
    token = CancellationToken()
    loop = AgentLoop(
        HangingProvider(),
        build_default_registry(guard),
        ToolContext(guard, AllowAllApproval(), cancellation_token=token),
        config=AgentConfig(max_steps=1, total_timeout_seconds=10, provider_timeout_seconds=10),
        cancellation_token=token,
    )

    async def run_case():
        task = asyncio.create_task(loop.run("hang"))
        await asyncio.sleep(0.05)
        loop.cancel("operator stop")
        return await asyncio.wait_for(task, timeout=1)

    result = asyncio.run(run_case())
    assert result.stopped_reason == "cancelled"
    assert result.state == "cancelled"
    assert not (tmp_path / "answer.txt").exists()


def test_agent_loop_timeout_records_unresolved_provider_attempt_and_recovery_state(tmp_path: Path):
    """A provider that ignores task cancellation must never look successful."""

    class IgnoresCancellationProvider:
        async def complete(self, _messages, _tools):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                # Simulate a blocking adapter which catches cancellation and
                # continues doing untrusted work in the detached task.
                await asyncio.sleep(0.2)
                return ModelResponse(Message("assistant", "late"), finish_reason="stop")

    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / ".forgecode" / "sessions" / "timeout.jsonl", run_id="timeout-run", mode="act")
    loop = AgentLoop(
        IgnoresCancellationProvider(),
        build_default_registry(guard),
        ToolContext(guard, AllowAllApproval(), run_id="timeout-run"),
        session=session,
        config=AgentConfig(max_steps=1, total_timeout_seconds=1, provider_timeout_seconds=0.03),
    )
    started = time.monotonic()
    result = asyncio.run(loop.run("wait"))
    elapsed = time.monotonic() - started
    # The provider is isolated on a daemon event-loop worker; filesystem
    # fsyncs and Windows thread scheduling can add a small bounded overhead,
    # but the caller must not wait for the provider's 30-second coroutine.
    assert elapsed < 1.0
    assert result.stopped_reason == "deadline_exceeded"
    assert result.state == "recovery_required"
    events = list(session.read(strict=True))
    attempts = [event.payload for event in events if event.kind == "provider_attempt"]
    assert attempts and any(item.get("unresolved") is True for item in attempts)
    assert any(item.get("outcome") in {"cancelled", "timeout", "deadline_exceeded", "unresolved"} for item in attempts)
    assert not any(event.kind == "model_message" and event.payload.get("content") == "late" for event in events)


def test_agent_loop_records_persistent_provider_attempts_once(tmp_path: Path):
    """A provider-owned event list must not be replayed on later loop turns."""

    class Provider:
        def __init__(self):
            self.calls = 0
            self.attempt_events = []

        async def complete(self, _messages, _tools, context=None):
            self.calls += 1
            self.attempt_events.append({
                "request_id": context.request_id if context else "request",
                "attempt_id": f"attempt-{self.calls}",
                "attempt": 1,
                "protocol": "fake",
                "outcome": "success",
                "unresolved": False,
            })
            if self.calls == 1:
                return ModelResponse(Message("assistant", tool_calls=(ToolCall("r", "read_file", {"path": "missing"}),)), finish_reason="tool_calls")
            return ModelResponse(Message("assistant", "done"), finish_reason="stop")

    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / ".forgecode" / "sessions" / "attempts.jsonl", run_id="attempt-run", mode="act")
    provider = Provider()
    result = asyncio.run(AgentLoop(provider, build_default_registry(guard), ToolContext(guard, AllowAllApproval(), run_id="attempt-run"), session=session, config=AgentConfig(max_steps=2)).run("read"))
    assert result.stopped_reason == "model_finished"
    events = [event for event in session.read(strict=True) if event.kind == "provider_attempt"]
    ids = [event.payload.get("attempt_id") for event in events]
    assert len(ids) == len(set(ids)) == 2


def test_agent_loop_retains_unresolved_side_effect_for_recovery(tmp_path: Path):
    class UnresolvedTool:
        definition = ToolDefinition("unresolved", "simulated unresolved side effect", {"type": "object"}, side_effecting=True)

        def execute(self, _arguments, _context):
            return ToolResult(False, "worker detached", {"error": "timeout", "termination_result": "unresolved"})

    class Provider:
        async def complete(self, _messages, _tools):
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("u", "unresolved", {}),)), finish_reason="tool_calls")

    guard = WorkspaceGuard(tmp_path)
    registry = ToolRegistry()
    registry.register(UnresolvedTool())
    session = SessionStore(tmp_path / ".forgecode" / "sessions" / "unresolved.jsonl", run_id="unresolved-run", mode="act")
    loop = AgentLoop(Provider(), registry, ToolContext(guard, AllowAllApproval(), run_id="unresolved-run"), session=session, config=AgentConfig(max_steps=1))
    result = asyncio.run(loop.run("do it"))
    assert result.stopped_reason == "recovery_conflict"
    assert result.state == "recovery_required"
    checkpoint = session.path.with_suffix(".checkpoint.json")
    assert checkpoint.exists()
    assert "\"id\":\"u\"" in checkpoint.read_text(encoding="utf-8")


class _PendingProbe:
    definition = ToolDefinition("probe_write", "probe a pending side effect", {"type": "object"}, side_effecting=True)

    def __init__(self, checkpoint_path: Path):
        self.checkpoint_path = checkpoint_path
        self.pending: tuple[dict, ...] = ()

    def execute(self, _arguments, _context):
        import json

        self.pending = tuple(json.loads(self.checkpoint_path.read_text(encoding="utf-8")).get("pending_actions", ()))
        return ToolResult(True, "probed")


class _ProbeProvider:
    def __init__(self):
        self.calls = 0

    async def complete(self, _messages, _tools):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("probe-1", "probe_write", {}),)), finish_reason="tool_calls")
        return ModelResponse(Message("assistant", "done"), finish_reason="stop")


def test_agent_loop_journals_pending_action_before_side_effect(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    session_path = tmp_path / ".forgecode" / "sessions" / "run.jsonl"
    session = SessionStore(session_path, run_id="pending-run", mode="act")
    registry = ToolRegistry()
    probe = _PendingProbe(session_path.with_suffix(".checkpoint.json"))
    registry.register(probe)
    result = asyncio.run(AgentLoop(_ProbeProvider(), registry, ToolContext(guard, AllowAllApproval(), run_id="pending-run"), session=session, config=AgentConfig(max_steps=3)).run("probe"))
    assert result.succeeded
    assert probe.pending and probe.pending[0]["id"] == "probe-1"


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def test_test_profile_teardown_failure_never_passes(tmp_path: Path):
    profile = _TestProfile("teardown", _python("print('main')"), teardown=_python("raise SystemExit(7)"))
    result = _TestProfileRunner(WorkspaceGuard(tmp_path), approval=AllowAllApproval()).run(profile)
    assert not result.ok
    assert result.verification_status == "failed"
    assert result.error_code == "teardown_failed"
    assert any(step.get("phase") == "teardown" and step.get("exit_code") == 7 for step in result.steps)


def test_test_profile_cancelled_after_main_never_passes(tmp_path: Path):
    marker = tmp_path / "main.done"
    profile = _TestProfile(
        "cancel-after-main",
        _python(f"from pathlib import Path; Path({str(marker)!r}).write_text('done'); print('main')"),
        teardown=_python("print('teardown')"),
    )
    result = _TestProfileRunner(WorkspaceGuard(tmp_path), approval=AllowAllApproval()).run(profile, cancel=lambda: marker.exists())
    assert not result.ok
    assert result.verification_status == "cancelled"
    assert result.error_code == "cancelled"


def test_test_profile_setup_timeout_is_top_level_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A bounded setup phase is part of verification evidence, not a hidden prelude."""
    import forgecode.testing as testing

    empty = __import__("hashlib").sha256(b"").hexdigest()

    def timeout(_runner, *_args, **_kwargs):
        return {
            "exit_code": None,
            "timed_out": True,
            "cancelled": False,
            "stdout": "",
            "stderr": "",
            "stdout_digest": empty,
            "stderr_digest": empty,
            "truncated": False,
            "termination_result": "requested",
            "cancellation_error": None,
        }

    monkeypatch.setattr(testing.TestProfileRunner, "_execute_argv", timeout)
    profile = _TestProfile("setup-timeout", _python("print('main')"), setup=_python("print('setup')"))
    result = _TestProfileRunner(WorkspaceGuard(tmp_path), approval=AllowAllApproval()).run(profile)
    assert not result.ok
    assert result.timed_out is True
    assert result.verification_status == "timed_out"
    assert result.error_code == "setup_timeout"


def test_test_profile_teardown_timeout_is_top_level_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import forgecode.testing as testing

    empty = __import__("hashlib").sha256(b"").hexdigest()

    def phase_outcome(_runner, command, *_args, **_kwargs):
        if command[-1] == "teardown":
            return {
                "exit_code": None,
                "timed_out": True,
                "cancelled": False,
                "stdout": "",
                "stderr": "",
                "stdout_digest": empty,
                "stderr_digest": empty,
                "truncated": False,
                "termination_result": "requested",
                "cancellation_error": None,
            }
        return {
            "exit_code": 0,
            "timed_out": False,
            "cancelled": False,
            "stdout": "",
            "stderr": "",
            "stdout_digest": empty,
            "stderr_digest": empty,
            "truncated": False,
            "termination_result": "already_exited",
            "cancellation_error": None,
        }

    monkeypatch.setattr(testing.TestProfileRunner, "_execute_argv", phase_outcome)
    profile = _TestProfile("teardown-timeout", _python("print('main')"), teardown=(sys.executable, "-c", "teardown"))
    result = _TestProfileRunner(WorkspaceGuard(tmp_path), approval=AllowAllApproval()).run(profile)
    assert not result.ok
    assert result.timed_out is True
    assert result.verification_status == "timed_out"
    assert result.error_code == "teardown_timeout"


class _RetryTransport:
    def __init__(self):
        self.calls = 0

    def post_json(self, *_args):
        self.calls += 1
        if self.calls == 1:
            return 503, b"temporary"
        return 200, b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"ok"}}]}'


def test_provider_retry_backoff_honors_cancellation():
    async def run_case():
        token = CancellationToken()
        transport = _RetryTransport()
        provider = OpenAICompatibleProvider(api_key="fake", base_url="https://example.test/v1", model="m", transport=transport, retry_base_delay=0.5, max_retries=2)
        task = asyncio.create_task(provider.complete([Message("user", "hi")], [], context=ProviderContext(cancellation_token=token)))
        while not provider.retry_events:
            await asyncio.sleep(0.005)
        token.cancel("stop retry")
        with pytest.raises(ProviderError) as error:
            await task
        return transport.calls, error.value

    calls, error = asyncio.run(run_case())
    assert calls == 1
    assert error.category == "cancelled"


def test_provider_attempt_event_marks_protocol_failure():
    class MalformedTransport:
        def post_json(self, *_args):
            return 200, b"{not-json"

    provider = OpenAICompatibleProvider(api_key="fake", base_url="https://example.test/v1", model="m", transport=MalformedTransport(), max_retries=0)
    with pytest.raises(ProviderError, match="malformed JSON"):
        asyncio.run(provider.complete([Message("user", "hi")], []))
    assert provider.attempt_events
    assert provider.attempt_events[0]["outcome"] == "error"
    assert provider.attempt_events[0]["error_category"] == "protocol_error"


def test_sse_parser_checks_cancellation_between_chunks():
    from forgecode.models.openai_compatible import _sse_json_events

    token = CancellationToken()

    def chunks():
        yield b'data: {"choices": [{"index": 0, "delta": {"content": "part"}}]}\n'
        token.cancel("stop stream")
        yield b'data: [DONE]\n'

    with pytest.raises(ProviderError, match="cancelled") as error:
        _sse_json_events(chunks(), cancellation=token)
    assert error.value.category == "cancelled"


def test_sse_parser_rejects_malformed_utf8_duplicate_frame_and_post_done():
    from forgecode.models.openai_compatible import _sse_json_events

    frame = b'data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n'
    with pytest.raises(ProviderError, match="malformed SSE JSON"):
        _sse_json_events([b"data: \xff\n", b"data: [DONE]\n"])
    with pytest.raises(ProviderError, match="repeated a data frame"):
        _sse_json_events([frame, frame, b"data: [DONE]\n"])
    with pytest.raises(ProviderError, match=r"after \[DONE\]"):
        _sse_json_events([b"data: [DONE]\n", frame])


def test_sse_parser_rejects_semantically_duplicate_frames_with_different_whitespace():
    from forgecode.models.openai_compatible import _sse_json_events

    first = b'data: {"choices":[{"index":0,"delta":{"content":"same"}}]}\n'
    duplicate = b'data:  { "choices" : [ { "index" : 0, "delta" : { "content" : "same" } } ] }\n'
    with pytest.raises(ProviderError, match="repeated a data frame"):
        _sse_json_events([first, duplicate, b"data: [DONE]\n"])


def test_sse_assembler_rejects_out_of_order_and_oversized_fragments():
    from forgecode.models.openai_compatible import assemble_chat_stream

    with pytest.raises(ProviderError, match="out-of-order"):
        assemble_chat_stream(
            [
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 1, "id": "b", "function": {"name": "b", "arguments": "{}"}}]}}]},
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "a", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]},
            ]
        )
    with pytest.raises(ProviderError, match="exceeded the configured size limit"):
        assemble_chat_stream(
            [{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "x", "function": {"name": "x", "arguments": "{" + ("a" * 200_001)}}]}, "finish_reason": "tool_calls"}]}]
        )


def test_provider_timeout_marks_unresolved_attempt():
    class SlowTransport:
        def post_json(self, *_args):
            time.sleep(0.2)
            return 200, b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"late"}}]}'

    provider = OpenAICompatibleProvider(api_key="fake", base_url="https://example.test/v1", model="m", transport=SlowTransport(), timeout=0.02, max_retries=0)
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.complete([Message("user", "hi")], [], context=ProviderContext(request_id="req-timeout")))
    assert error.value.unresolved is True
    assert error.value.request_id == "req-timeout"
    assert provider.attempt_events[0]["unresolved"] is True
    assert provider.attempt_events[0]["outcome"] == "error"


def test_provider_final_http_error_attempt_is_not_reported_success():
    class ErrorTransport:
        def post_json(self, *_args):
            return 400, b'{"error":{"message":"bad request"}}'

    provider = OpenAICompatibleProvider(api_key="fake", base_url="https://example.test/v1", model="m", transport=ErrorTransport(), max_retries=0)
    with pytest.raises(ProviderError):
        asyncio.run(provider.complete([Message("user", "hi")], []))
    assert provider.attempt_events[0]["outcome"] == "error"
    assert provider.attempt_events[0]["error_category"] == "http_error"
