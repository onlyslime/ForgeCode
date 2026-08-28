"""Focused durability regressions for provider and runtime evidence."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from forgecode.agent import AgentConfig, AgentLoop
from forgecode.models import Message, ModelResponse, OpenAICompatibleProvider, ProviderContext, ProviderError, ToolCall
from forgecode.security.workspace import WorkspaceGuard
from forgecode.storage import SessionStore, TransactionError, TransactionStore
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry


def test_provider_attempt_identity_includes_request_id(tmp_path: Path):
    """Reused per-turn attempt ids must not hide later request evidence."""

    class Provider:
        def __init__(self):
            self.calls = 0
            self.attempt_events: list[dict] = []

        async def complete(self, _messages, _tools, context=None):
            self.calls += 1
            self.attempt_events.append(
                {
                    "request_id": context.request_id,
                    "attempt_id": "attempt-1",  # intentionally reused
                    "attempt": 1,
                    "protocol": "fake",
                    "outcome": "success",
                    "unresolved": False,
                }
            )
            if self.calls == 1:
                return ModelResponse(
                    Message("assistant", tool_calls=(ToolCall("read-1", "read_file", {"path": "missing.txt"}),)),
                    finish_reason="tool_calls",
                )
            return ModelResponse(Message("assistant", "done"), finish_reason="stop")

    workspace = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / "run.jsonl", run_id="identity-run", mode="act")
    provider = Provider()
    result = asyncio.run(
        AgentLoop(
            provider,
            build_default_registry(workspace),
            ToolContext(workspace, AllowAllApproval(), run_id="identity-run"),
            session=session,
            config=AgentConfig(max_steps=2),
        ).run("read")
    )
    assert result.succeeded
    attempts = [event.payload for event in session.read(strict=True) if event.kind == "provider_attempt"]
    assert len(attempts) == 2
    assert {item["request_id"] for item in attempts} == {"identity-run:0", "identity-run:1"}


def test_unresolved_provider_snapshot_overrides_late_success(tmp_path: Path):
    """A detached provider attempt cannot be journaled as a clean success."""

    class Provider:
        def __init__(self):
            self.attempt_events: list[dict] = []

        async def complete(self, _messages, _tools, context=None):
            self.attempt_events.append(
                {
                    "request_id": context.request_id,
                    "attempt_id": "attempt-1",
                    "attempt": 1,
                    "protocol": "fake",
                    # Simulate an adapter racing the caller and publishing a
                    # success marker before its worker is detached.
                    "outcome": "success",
                    "unresolved": False,
                }
            )
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)
                return ModelResponse(Message("assistant", "late"), finish_reason="stop")

    workspace = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / "run.jsonl", run_id="late-run", mode="act")
    result = asyncio.run(
        AgentLoop(
            Provider(),
            build_default_registry(workspace),
            ToolContext(workspace, AllowAllApproval(), run_id="late-run"),
            session=session,
            config=AgentConfig(max_steps=1, provider_timeout_seconds=0.03, provider_cleanup_grace_seconds=0.01),
        ).run("wait")
    )
    assert result.stopped_reason == "deadline_exceeded"
    attempts = [event.payload for event in session.read(strict=True) if event.kind == "provider_attempt"]
    assert attempts and attempts[0]["unresolved"] is True
    assert attempts[0]["outcome"] == "unresolved"


def test_session_rejects_append_when_envelope_cannot_fit_bound(tmp_path: Path):
    """Payload truncation must not leave an oversized, unreadable event."""

    store = SessionStore(tmp_path / "tiny.jsonl", max_event_chars=128)
    try:
        store.append("event", {"content": "x" * 10_000})
    except ValueError as exc:
        assert "event envelope" in str(exc)
    else:  # pragma: no cover - documents the safety invariant
        raise AssertionError("append unexpectedly produced an oversized event")
    assert not store.path.exists()


def test_transaction_list_contains_manifest_alias_errors(tmp_path: Path, monkeypatch):
    """A raced alias must be reported as a ledger issue, never escape list()."""

    workspace = WorkspaceGuard(tmp_path)
    store = TransactionStore(workspace)
    store.manifest_dir.mkdir(parents=True)
    (store.manifest_dir / "raced.json").write_text("{}", encoding="utf-8")
    original_resolve = workspace.resolve

    def reject_raced(path, *args, **kwargs):
        if Path(path).name == "raced.json":
            from forgecode.security.workspace import WorkspaceViolation

            raise WorkspaceViolation("raced alias")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(workspace, "resolve", reject_raced)
    assert store.list() == ()
    assert store.last_list_issues and "raced.json" in store.last_list_issues[0]
    with pytest.raises(TransactionError, match="unsafe"):
        store.load("raced")


def test_transaction_quota_accounting_rejects_runtime_alias(tmp_path: Path, monkeypatch):
    """Quota scans must not follow an aliased runtime file."""

    workspace = WorkspaceGuard(tmp_path)
    store = TransactionStore(workspace)
    rogue = store.root / "rogue-entry"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_bytes(b"outside-target-placeholder")
    original_resolve = workspace.resolve

    def reject_rogue(path, *args, **kwargs):
        if Path(path).name == rogue.name:
            from forgecode.security.workspace import WorkspaceViolation

            raise WorkspaceViolation("runtime alias")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(workspace, "resolve", reject_rogue)
    with pytest.raises(TransactionError, match="unsafe"):
        store._runtime_bytes()


def test_undo_parent_cas_failure_does_not_leave_false_committed_child(tmp_path: Path, monkeypatch):
    """A post-commit parent race must be durable recovery evidence.

    The file rollback is expected to restore the original after-bytes when no
    external edit occurred, but the child undo manifest itself must not remain
    ``committed``: that state would falsely claim that the parent was marked
    undone and that the restored bytes are the durable result.
    """
    workspace = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_bytes(b"before")
    store = TransactionStore(workspace)
    result = build_default_registry(workspace).execute(
        "write_file",
        {"path": "a.txt", "content": "after"},
        ToolContext(workspace, AllowAllApproval(), transaction_store=store, run_id="parent-race"),
    )
    assert result.ok and target.read_bytes() == b"after"
    original_id = result.metadata["transaction_id"]
    original_save_cas = store._save_cas

    def fail_parent_update(manifest, *, expected=None):
        if manifest.transaction_id == original_id and manifest.state == "undone":
            raise TransactionError("simulated parent CAS race")
        return original_save_cas(manifest, expected=expected)

    monkeypatch.setattr(store, "_save_cas", fail_parent_update)
    with pytest.raises(TransactionError, match="undo recovery conflict"):
        store.undo(original_id, approval=AllowAllApproval(), run_id="undo-race")

    # The child commit is durable, so no compensating write is attempted: the
    # restored bytes remain in place and both records expose the metadata race
    # as an explicit recovery condition.
    assert target.read_bytes() == b"before"
    assert store.load(original_id).state == "recovery_required"
    children = [item for item in store.list(limit=20) if item.tool == "undo_transaction"]
    assert len(children) == 1
    assert children[0].state == "recovery_required"
    assert children[0].error and "parent CAS race" in children[0].error


def test_session_cross_process_append_sequences_are_contiguous(tmp_path: Path):
    """The OS lock must protect sequence allocation between workers."""

    path = tmp_path / "shared.jsonl"
    worker = textwrap.dedent(
        """
        from pathlib import Path
        import sys
        from forgecode.storage import SessionStore
        store = SessionStore(Path(sys.argv[1]), run_id="shared-run")
        for index in range(6):
            store.append("worker_event", {"index": index})
        """
    )
    root = Path(__file__).resolve().parents[1]
    processes = [
        subprocess.Popen([sys.executable, "-c", worker, str(path)], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(3)
    ]
    results = [process.communicate(timeout=20) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    events = SessionStore(path).read_with_issues(strict=True).events
    assert [event.sequence for event in events] == list(range(1, 19))


def test_provider_does_not_retry_unresolved_transport_timeout():
    """A detached request must not be duplicated by retry backoff."""

    class SlowTransport:
        def __init__(self):
            self.calls = 0

        def post_json(self, *_args):
            self.calls += 1
            import time

            time.sleep(0.15)
            return 200, b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"late"}}]}'

    transport = SlowTransport()
    provider = OpenAICompatibleProvider(
        api_key="fake",
        base_url="https://example.test/v1",
        model="m",
        transport=transport,
        timeout=0.01,
        max_retries=3,
        retry_base_delay=0,
    )
    with pytest.raises(ProviderError) as raised:
        asyncio.run(provider.complete([Message("user", "wait")], [], context=ProviderContext(request_id="timeout-request")))
    assert raised.value.unresolved is True
    assert transport.calls == 1
    assert len(provider.retry_events) == 0


def test_provider_request_limit_error_keeps_request_identity():
    provider = OpenAICompatibleProvider(
        api_key="fake",
        base_url="https://example.test/v1",
        model="m",
        max_request_bytes=1,
        max_retries=0,
    )
    with pytest.raises(ProviderError) as raised:
        asyncio.run(provider.complete([Message("user", "hello")], [], context=ProviderContext(request_id="oversized-request")))
    assert raised.value.category == "request_limit"
    assert raised.value.request_id == "oversized-request"


def test_agent_loop_contains_provider_system_exit(tmp_path: Path):
    """A provider BaseException must not terminate the hosting process."""

    class ExitingProvider:
        async def complete(self, _messages, _tools):
            raise SystemExit("provider attempted to exit")

    workspace = WorkspaceGuard(tmp_path)
    result = asyncio.run(
        AgentLoop(
            ExitingProvider(),
            build_default_registry(workspace),
            ToolContext(workspace, AllowAllApproval()),
            config=AgentConfig(max_steps=1),
        ).run("safe")
    )
    assert result.stopped_reason == "provider_error"
    assert result.state == "failed"


def test_transport_system_exit_is_contained_by_provider():
    class ExitingTransport:
        def post_json(self, *_args):
            raise SystemExit("transport attempted to exit")

    provider = OpenAICompatibleProvider(
        api_key="fake",
        base_url="https://example.test/v1",
        model="m",
        transport=ExitingTransport(),
        max_retries=0,
    )
    with pytest.raises(ProviderError) as raised:
        asyncio.run(provider.complete([Message("user", "safe")], []))
    assert raised.value.category == "transport_error"


def test_profile_does_not_run_teardown_after_unresolved_main(tmp_path: Path, monkeypatch):
    """An unresolved main process cannot overlap a cleanup command."""

    import hashlib
    import forgecode.testing as testing

    empty = hashlib.sha256(b"").hexdigest()
    calls: list[tuple[str, ...]] = []

    def fake_execute(_runner, command, *_args, **_kwargs):
        calls.append(tuple(command))
        return {
            "exit_code": None,
            "timed_out": True,
            "cancelled": False,
            "stdout": "",
            "stderr": "",
            "stdout_digest": empty,
            "stderr_digest": empty,
            "truncated": False,
            "termination_result": "unresolved",
            "cancellation_error": None,
        }

    monkeypatch.setattr(testing.TestProfileRunner, "_execute_argv", fake_execute)
    profile = testing.TestProfile(
        "unresolved-main",
        (sys.executable, "-c", "main"),
        teardown=(sys.executable, "-c", "teardown"),
    )
    evidence = testing.TestProfileRunner(WorkspaceGuard(tmp_path), approval=AllowAllApproval()).run(profile)
    assert not evidence.ok
    assert evidence.verification_status == "error"
    assert evidence.error_code == "termination_unresolved"
    assert len(calls) == 1


def test_profile_reader_cleanup_unresolved_cannot_pass(tmp_path: Path, monkeypatch):
    """A pipe reader that outlives cleanup invalidates verification."""

    import forgecode.testing as testing

    class StuckReader:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return None

        def join(self, *_args, **_kwargs):
            return None

        def is_alive(self):
            return True

    monkeypatch.setattr(testing.threading, "Thread", StuckReader)
    profile = testing.TestProfile("reader-unresolved", (sys.executable, "-c", "print('ok')"))
    evidence = testing.TestProfileRunner(WorkspaceGuard(tmp_path), approval=AllowAllApproval()).run(profile)
    assert not evidence.ok
    assert evidence.verification_status == "error"
    assert evidence.error_code == "termination_unresolved"
