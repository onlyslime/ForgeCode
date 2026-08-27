import asyncio
from pathlib import Path

import pytest

from forgecode.agent import AgentConfig, AgentLoop
from forgecode.models import Message, ModelResponse, OpenAICompatibleProvider, ProviderError, ToolCall
from forgecode.security import WorkspaceGuard
from forgecode.storage import Checkpoint, CheckpointStore, SessionStore
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry
from forgecode.tools import ToolRegistry


class FlakyTransport:
    def __init__(self):
        self.calls = 0

    def post_json(self, url, headers, body, timeout):
        self.calls += 1
        if self.calls == 1:
            return 503, b'{"error":{"message":"temporary"}}'
        return 200, b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'


def test_provider_retries_transient_http_without_leaking_secret():
    transport = FlakyTransport()
    provider = OpenAICompatibleProvider(api_key="secret-key", base_url="https://example.test/v1", model="m", transport=transport, retry_base_delay=0)
    result = asyncio.run(provider.complete([Message("user", "hi")], []))
    assert result.message.content == "ok"
    assert transport.calls == 2
    assert provider.retry_events[0]["category"] == "http_503"
    assert "secret-key" not in str(provider.retry_events)


def test_provider_rejects_duplicate_tool_ids():
    from forgecode.models import parse_chat_completion
    with pytest.raises(ProviderError, match="repeats tool call id"):
        parse_chat_completion({"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "x", "function": {"name": "a", "arguments": "{}"}}, {"id": "x", "function": {"name": "b", "arguments": "{}"}}]}}]})


def test_checkpoint_round_trip_and_conflict(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("one", encoding="utf-8")
    checkpoint = Checkpoint.create(guard, run_id="r", state="paused", mode="act", sequence=4, files=["a.txt"], pending_actions=[{"tool": "write_file"}])
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.save(checkpoint)
    loaded = store.load()
    assert loaded.run_id == "r" and loaded.files[0].sha256
    assert not store.validate(loaded, guard)
    target.write_text("two", encoding="utf-8")
    conflicts = store.validate(loaded, guard, expected_run_id="r")
    assert conflicts and conflicts[0].path == "a.txt"


def test_checkpoint_redacts_pending_side_effect_arguments(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    checkpoint = Checkpoint.create(guard, run_id="r", state="paused", mode="act", sequence=1, pending_actions=[{"tool": "run_command", "command": "token=do-not-leak"}], secrets=["do-not-leak"])
    store = CheckpointStore(tmp_path / "c.json")
    store.save(checkpoint)
    assert "do-not-leak" not in (tmp_path / "c.json").read_text(encoding="utf-8")


def test_loop_result_contains_structured_verification(tmp_path: Path):
    class Provider:
        calls = 0
        async def complete(self, messages, tools):
            self.calls += 1
            return ModelResponse(Message("assistant", "done"))
    guard = WorkspaceGuard(tmp_path)
    result = asyncio.run(AgentLoop(Provider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval()), config=AgentConfig(verification_command="python -c \"print('ok')\"", max_verification_attempts=1)).run("verify"))
    assert result.succeeded and result.verifications and result.verifications[0].ok


def test_patch_rejects_external_change_after_approval(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("one\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)

    class Approval:
        def approve(self, tool_name, arguments):
            target.write_text("external\n", encoding="utf-8")
            return True

    result = registry.execute("apply_patch", {"patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-one\n+two\n"}, ToolContext(guard, Approval()))
    assert not result.ok and result.metadata["error"] == "concurrency_conflict"
    assert target.read_text(encoding="utf-8") == "external\n"


def test_loop_scrubs_secret_from_provider_context(tmp_path: Path):
    observed = []
    class Provider:
        async def complete(self, messages, tools):
            observed.append("\n".join(message.content for message in messages))
            return ModelResponse(Message("assistant", "done"))
    guard = WorkspaceGuard(tmp_path)
    asyncio.run(AgentLoop(Provider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval(), secrets=("secret-value",))).run("task secret-value"))
    assert observed and "secret-value" not in observed[0] and "REDACTED" in observed[0]
