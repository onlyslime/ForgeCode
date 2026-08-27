import hashlib
import io
from pathlib import Path

import pytest

from forgecode.agent import ContextCompactor, SessionContextRebuilder
from forgecode.application.commands import main
from forgecode.application import InteractiveSession
from forgecode.security import WorkspaceGuard, WorkspaceViolation
from forgecode.storage import SessionStore, TransactionError, TransactionStore
from forgecode.storage import Checkpoint, CheckpointStore, SessionFormatError
from forgecode.tools import AgentMode, AllowAllApproval, ToolContext, build_default_registry


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_persistent_transaction_review_and_restart_safe_undo(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_bytes(b"before\r\n")
    store = TransactionStore(guard)
    result = build_default_registry(guard).execute("write_file", {"path": "a.txt", "content": "after\r\n"}, ToolContext(guard, AllowAllApproval(), transaction_store=store, run_id="run-1"))
    assert result.ok and target.read_bytes() == b"after\r\n"
    restarted = TransactionStore(guard)
    review = restarted.review(result.metadata["transaction_id"])
    assert review["rollback_available"] is True
    undone = restarted.undo(result.metadata["transaction_id"], approval=AllowAllApproval(), run_id="run-2")
    assert undone.state == "committed" and target.read_bytes() == b"before\r\n"
    with pytest.raises(TransactionError, match="already undone|not committed"):
        restarted.undo(result.metadata["transaction_id"], approval=AllowAllApproval(), run_id="run-3")
    undo_manifest = restarted.latest(committed_only=True)
    assert undo_manifest.tool == "undo_transaction"
    assert not restarted.preview_undo(undo_manifest.transaction_id).available


def test_undo_conflict_never_overwrites_external_edit(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    store = TransactionStore(guard)
    result = build_default_registry(guard).execute("write_file", {"path": "a.txt", "content": "after"}, ToolContext(guard, AllowAllApproval(), transaction_store=store, run_id="r"))
    target.write_text("external", encoding="utf-8")
    with pytest.raises(TransactionError, match="undo conflict"):
        store.undo(result.metadata["transaction_id"], approval=AllowAllApproval(), run_id="u")
    assert target.read_text(encoding="utf-8") == "external"


def test_undo_rechecks_hash_after_approval_before_writing(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    store = TransactionStore(guard)
    result = build_default_registry(guard).execute(
        "write_file",
        {"path": "a.txt", "content": "after"},
        ToolContext(guard, AllowAllApproval(), transaction_store=store, run_id="r"),
    )
    assert result.ok

    class EditDuringApproval:
        def approve(self, _tool_name, _arguments):
            target.write_text("external-after-approval", encoding="utf-8")
            return True

    with pytest.raises(TransactionError, match="undo conflict"):
        store.undo(result.metadata["transaction_id"], approval=EditDuringApproval(), run_id="u")
    assert target.read_text(encoding="utf-8") == "external-after-approval"


def test_create_transaction_undo_removes_only_expected_file(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    store = TransactionStore(guard)
    result = build_default_registry(guard).execute("write_file", {"path": "new.txt", "content": "new"}, ToolContext(guard, AllowAllApproval(), transaction_store=store, run_id="r"))
    assert result.ok and (tmp_path / "new.txt").exists()
    store.undo(result.metadata["transaction_id"], approval=AllowAllApproval(), run_id="u")
    assert not (tmp_path / "new.txt").exists()


def test_corrupt_backup_is_detected_before_undo(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a"
    target.write_bytes(b"old")
    store = TransactionStore(guard)
    result = build_default_registry(guard).execute("write_file", {"path": "a", "content": "new"}, ToolContext(guard, AllowAllApproval(), transaction_store=store, run_id="r"))
    manifest = store.load(result.metadata["transaction_id"])
    (store.blob_dir / manifest.operations[0].backup_sha256).write_bytes(b"corrupt")
    preview = store.preview_undo(manifest.transaction_id)
    assert not preview.available and any("hash" in conflict for conflict in preview.conflicts)


def test_transaction_operation_schema_rejects_incoherent_hashes_and_unicode_ids():
    from forgecode.storage.transaction import TransactionManifest, TransactionOperation

    operation = TransactionOperation("a.txt", "create", "0" * 64, "1" * 64, 1, 1, "0" * 64)
    with pytest.raises(TransactionError, match="inconsistent"):
        operation.validate()

    valid = TransactionOperation("a.txt", "create", None, "1" * 64, 0, 1, None)
    manifest = TransactionManifest("事务", "run", "2026-08-27T00:00:00+00:00", "write_file", "committed", (valid,))
    with pytest.raises(TransactionError, match="transaction_id"):
        manifest.validate()


def test_transaction_manifest_requires_timezone_timestamp():
    from forgecode.storage.transaction import TransactionManifest, TransactionOperation

    operation = TransactionOperation("a.txt", "create", None, "1" * 64, 0, 1, None)
    manifest = TransactionManifest("tx", "run", "2026-08-27T00:00:00", "write_file", "committed", (operation,))
    with pytest.raises(TransactionError, match="timezone"):
        manifest.validate()


def test_compaction_appends_event_without_rewriting_original_prefix(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", run_id="r")
    for index in range(40):
        store.append("user_message", {"content": f"message {index}"})
    before = store.path.read_bytes()
    result = ContextCompactor(max_chars=2_000, recent_events=5).compact_store(store)
    after = store.path.read_bytes()
    assert after.startswith(before) and len(after) > len(before)
    assert result.omitted_events == 35 and "recent_messages" in result.retained_sections


def test_compaction_rejects_corrupt_session_without_appending(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", run_id="r")
    store.append("user_message", {"content": "one"})
    with store.path.open("a", encoding="utf-8") as stream:
        stream.write('{"broken"\n')
    before = store.path.read_bytes()
    with pytest.raises(SessionFormatError, match="inconsistent session"):
        ContextCompactor().compact_store(store)
    assert store.path.read_bytes() == before


def test_context_rebuilder_preserves_evidence_without_replay(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl", run_id="r")
    store.append("user_message", {"content": "fix"})
    store.append("patch_commit", {"transaction_id": "t", "ok": True})
    store.append("verification_result", {"ok": False, "exit_code": 1})
    store.append("state_transition", {"to": "paused"})
    rebuilt = SessionContextRebuilder().rebuild(store)
    assert rebuilt.state == "paused" and rebuilt.transaction_evidence[0]["transaction_id"] == "t"
    assert rebuilt.verification_evidence[0]["ok"] is False
    assert all(message.role != "system" for message in rebuilt.messages)


def test_context_rebuilder_marks_legacy_and_checkpoint_sequence_conflicts(tmp_path: Path):
    import json

    legacy_path = tmp_path / "legacy.jsonl"
    legacy_path.write_text(json.dumps({"kind": "user_message", "payload": {"content": "old"}, "timestamp": "2026-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
    legacy = SessionContextRebuilder().rebuild(SessionStore(legacy_path))
    assert "legacy session schema is inspect-only" in legacy.conflicts

    store = SessionStore(tmp_path / "run.jsonl", run_id="r")
    store.append("user_message", {"content": "new"})
    checkpoint = Checkpoint.create(WorkspaceGuard(tmp_path), run_id="r", state="paused", mode="act", sequence=99)
    rebuilt = SessionContextRebuilder().rebuild(store, checkpoint)
    assert any("checkpoint sequence" in conflict for conflict in rebuilt.conflicts)


def test_interactive_dispatch_has_real_commands_fifo_and_never_sends_slash_to_provider():
    sent = []
    calls = []
    service = InteractiveSession(lambda message: sent.append(message) or {"message": message}, status=lambda: calls.append("status") or {"state": "ok"}, compact=lambda: calls.append("compact") or {"omitted": 1}, output=lambda _text: None)
    assert "/status" not in sent
    service.dispatch("/status")
    service.dispatch("hello")
    service.enqueue("one"); service.enqueue("two")
    service.drain()
    assert sent == ["hello", "one", "two"] and calls == ["status"]
    assert "/compact" in service.help_text() and service.dispatch("/compact") == {"omitted": 1}


def test_interactive_unknown_plan_denial_and_quit_are_recoverable():
    service = InteractiveSession(lambda message: message, set_mode=lambda mode: {"error": "approval denied"} if mode == "act" else {"mode": mode}, output=lambda _text: None)
    results = service.run_stream(["/unknown\n", "/mode act\n", "/quit\n", "ignored\n"])
    assert "unknown command" in results[0]["error"]
    assert results[1]["error"] == "approval denied"
    assert results[2]["stopped"] is True and service.stopped


def test_interactive_quit_invokes_checkpoint_callback():
    calls = []
    service = InteractiveSession(lambda message: message, quit=lambda: calls.append("checkpoint") or {"stopped": True, "checkpointed": True}, output=lambda _text: None)

    result = service.dispatch("/quit")

    assert result["checkpointed"] is True and calls == ["checkpoint"] and service.stopped


def test_rule_change_after_approval_blocks_write_and_command(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)
    allowed = {"value": True}
    context = ToolContext(guard, AllowAllApproval(), mode="act", pre_side_effect_check=lambda: allowed["value"] or "rules changed")
    allowed["value"] = False
    write = registry.execute("write_file", {"path": "a.txt", "content": "x"}, context)
    command = registry.execute("run_command", {"command": "python -c \"print('should-not-run')\""}, context)
    assert not write.ok and write.metadata["error"] == "stale_context"
    assert not command.ok and command.metadata["error"] == "stale_context"
    assert not (tmp_path / "a.txt").exists()


def test_transaction_prepare_or_manifest_failure_does_not_leave_mutation(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")

    class BrokenStore:
        def prepare(self, **_kwargs):
            raise OSError("disk full")

    result = build_default_registry(guard).execute("write_file", {"path": "a.txt", "content": "after"}, ToolContext(guard, AllowAllApproval(), transaction_store=BrokenStore(), run_id="r"))
    assert not result.ok and result.metadata["error"] == "transaction_prepare_failed"
    assert target.read_text(encoding="utf-8") == "before"


def test_transaction_quota_is_checked_before_blob_write(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "large.txt"
    before = b"x" * 2_000
    target.write_bytes(before)
    store = TransactionStore(guard, max_total_bytes=2_048)
    with pytest.raises(TransactionError, match="retention limit"):
        store.prepare(
            transaction_id="quota-test",
            run_id="run",
            tool="write_file",
            operations=[{"path": "large.txt", "operation": "update", "before_sha256": sha(before), "after_sha256": sha(b"new"), "before_bytes": len(before), "after_bytes": 3}],
            before_bytes={"large.txt": before},
        )
    blob_files = list((tmp_path / ".forgecode" / "transactions" / "blobs").glob("*")) if (tmp_path / ".forgecode" / "transactions" / "blobs").is_dir() else []
    manifest_files = list((tmp_path / ".forgecode" / "transactions" / "manifests").glob("*")) if (tmp_path / ".forgecode" / "transactions" / "manifests").is_dir() else []
    assert blob_files == [] and manifest_files == []


def test_transaction_failed_manifest_save_cleans_new_blobs(tmp_path: Path, monkeypatch):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    before = b"before"
    target.write_bytes(before)
    store = TransactionStore(guard)

    def fail_save(_manifest):
        raise OSError("manifest disk full")

    monkeypatch.setattr(store, "_save", fail_save)
    with pytest.raises(OSError, match="manifest disk full"):
        store.prepare(
            transaction_id="save-failure",
            run_id="run",
            tool="write_file",
            operations=[{"path": "a.txt", "operation": "update", "before_sha256": sha(before), "after_sha256": sha(b"after"), "before_bytes": len(before), "after_bytes": 5}],
            before_bytes={"a.txt": before},
        )
    blob_dir = tmp_path / ".forgecode" / "transactions" / "blobs"
    assert not list(blob_dir.glob("*")) if blob_dir.is_dir() else True


def test_transaction_list_surfaces_corrupt_manifest_diagnostic(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    manifest_dir = tmp_path / ".forgecode" / "transactions" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "bad.json").write_text("{not-json", encoding="utf-8")
    store = TransactionStore(guard)
    assert store.list() == ()
    assert store.last_list_issues and "bad.json" in store.last_list_issues[0]
    with pytest.raises(TransactionError, match="corrupt manifests"):
        store.latest()


def test_transaction_review_surfaces_corrupt_peer_and_rejects_path_aliases(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    store = TransactionStore(guard)
    result = build_default_registry(guard).execute(
        "write_file",
        {"path": "a.txt", "content": "after"},
        ToolContext(guard, AllowAllApproval(), transaction_store=store, run_id="r"),
    )
    (store.manifest_dir / "corrupt.json").write_text("{broken", encoding="utf-8")
    review = store.review(result.metadata["transaction_id"])
    assert review["store_issues"] and "corrupt.json" in review["store_issues"][0]

    raw = store.load(result.metadata["transaction_id"]).to_dict()
    for unsafe in ("../a.txt", "/absolute.txt", "folder\\alias.txt", "a/./b"):
        raw["operations"][0]["path"] = unsafe
        with pytest.raises(TransactionError, match="invalid path"):
            __import__("forgecode.storage.transaction", fromlist=["TransactionManifest"]).TransactionManifest.from_dict(raw)


def test_persistent_stores_reject_runtime_symlink_aliases(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="symlink or junction"):
        SessionStore(alias / "run.jsonl")
    with pytest.raises(WorkspaceViolation, match="symlink or junction"):
        TransactionStore(guard, root=alias / "transactions")


def test_transaction_list_does_not_follow_replaced_manifest_directory(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    store = TransactionStore(guard)
    store.manifest_dir.mkdir(parents=True)
    real = tmp_path / "real-manifests"
    real.mkdir()
    original = store.manifest_dir
    try:
        original.rmdir()
        original.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert store.list() == ()
    assert store.last_list_issues and "unsafe" in store.last_list_issues[0]


def test_loop_stale_context_finishes_in_recovery_required(tmp_path: Path):
    import asyncio
    from forgecode.agent import AgentConfig, AgentLoop
    from forgecode.models import Message, ModelResponse, ToolCall

    class Provider:
        async def complete(self, messages, tools):
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("w", "write_file", {"path": "a", "content": "x"}),)))

    guard = WorkspaceGuard(tmp_path)
    store = SessionStore(tmp_path / "run.jsonl", run_id="r")
    result = asyncio.run(AgentLoop(Provider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval(), mode="act", pre_side_effect_check=lambda: "rules changed"), session=store, config=AgentConfig(max_steps=2)).run("fix"))
    assert result.stopped_reason == "recovery_conflict" and result.state == "recovery_required"
    assert not (tmp_path / "a").exists()


def test_broken_sse_through_provider_and_loop_has_zero_tool_side_effects(tmp_path: Path):
    import asyncio
    from forgecode.agent import AgentConfig, AgentLoop
    from forgecode.models import Message, OpenAICompatibleProvider

    class BrokenStreamTransport:
        def __init__(self, chunks):
            self.chunks = chunks
            self.calls = 0

        def post_stream(self, *_args):
            self.calls += 1
            return 200, iter(self.chunks)

        def post_json(self, *_args):
            raise AssertionError("broken stream must not fall back to non-stream")

    cases = (
        (b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"x","function":{"name":"write_file","arguments":"{\\"path\\":"}}]}}]}\n', b"data: [DONE]\n"),
        (b'data: {"choices": [{"index": 0, "delta": {"content": "partial"}}]}\n',),
        (b"data: not-json\n",),
    )
    for chunks in cases:
        target = tmp_path / "target.txt"
        target.write_text("before", encoding="utf-8")
        guard = WorkspaceGuard(tmp_path)
        registry = build_default_registry(guard)
        store = SessionStore(tmp_path / ("run-" + str(len(list(tmp_path.glob("run-*.jsonl")))) + ".jsonl"), run_id="r1")
        provider = OpenAICompatibleProvider(api_key="key", base_url="https://example.test/v1", model="m", transport=BrokenStreamTransport(chunks), streaming=True)
        result = asyncio.run(AgentLoop(provider, registry, ToolContext(guard, AllowAllApproval(), mode=AgentMode.ACT, transaction_store=TransactionStore(guard), run_id="r1"), session=store, config=AgentConfig(max_steps=1)).run("edit target"))
        assert result.stopped_reason == "provider_error"
        assert not list((tmp_path / ".forgecode" / "transactions" / "manifests").glob("*.json")) if (tmp_path / ".forgecode" / "transactions" / "manifests").is_dir() else True
        assert target.read_text(encoding="utf-8") == "before"


def test_session_read_rejects_invalid_utf8_and_reports_bounded_issue(tmp_path: Path):
    path = tmp_path / "broken.jsonl"
    path.write_bytes(b'{"kind":"user_message","payload":{},"timestamp":"2026-01-01T00:00:00+00:00"}\n\xff')
    result = SessionStore(path).read_with_issues()
    assert not result.events and result.issues and "UTF-8" in result.issues[0].message
    with pytest.raises(SessionFormatError, match="UTF-8"):
        SessionStore(path).read_with_issues(strict=True)


def test_session_read_rejects_invalid_timestamp_and_oversized_line(tmp_path: Path):
    invalid = tmp_path / "invalid-time.jsonl"
    invalid.write_text('{"kind":"x","payload":{},"timestamp":"not-a-time","schema_version":1,"run_id":"r","sequence":1}\n', encoding="utf-8")
    result = SessionStore(invalid).read_with_issues()
    assert result.issues and "timestamp" in result.issues[0].message

    oversized = tmp_path / "oversized.jsonl"
    oversized.write_text("x" * 300 + "\n", encoding="utf-8")
    result = SessionStore(oversized, max_event_chars=128).read_with_issues()
    assert result.issues and "safety limit" in result.issues[0].message


def test_json_rollback_approval_prompt_is_stderr_only(tmp_path: Path, monkeypatch, capsys):
    guard = WorkspaceGuard(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    store = TransactionStore(guard)
    result = build_default_registry(guard).execute(
        "write_file", {"path": "a.txt", "content": "after"},
        ToolContext(guard, AllowAllApproval(), transaction_store=store, run_id="r"),
    )
    assert result.ok
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    code = main(["--workspace", str(tmp_path), "rollback", result.metadata["transaction_id"], "--json"])
    captured = capsys.readouterr()
    import json
    payload = json.loads(captured.out)
    assert code != 0 and payload["ok"] is False
    assert "Approve" not in captured.out
    assert "Approve" in captured.err
    assert target.read_text(encoding="utf-8") == "after"


def test_diff_reports_corrupt_session_with_nonzero_exit_and_json_issues(tmp_path: Path, capsys):
    session_dir = tmp_path / ".forgecode" / "sessions"
    session_dir.mkdir(parents=True)
    (session_dir / "bad.jsonl").write_bytes(b"{not-json\n")
    code = main(["--workspace", str(tmp_path), "diff", "--session", "bad", "--json"])
    captured = capsys.readouterr()
    import json
    payload = json.loads(captured.out)
    assert code == 1 and payload["issues"]


def test_resume_conflict_on_completed_session_never_leaks_traceback(tmp_path: Path, capsys):
    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / ".forgecode" / "sessions" / "run.jsonl", run_id="run", mode="act")
    session.path.parent.mkdir(parents=True, exist_ok=True)
    session.append("run_created", {"mode": "act"}, mode="act")
    session.append("state_transition", {"from": "created", "to": "discovering"}, mode="act")
    session.append("state_transition", {"from": "discovering", "to": "planning"}, mode="act")
    session.append("state_transition", {"from": "planning", "to": "completed"}, mode="act")
    # A checkpoint pointing at a missing/changed file forces recovery conflict
    # while the parent lifecycle is already terminal.
    checkpoint = Checkpoint.create(guard, run_id="run", state="completed", mode="act", sequence=session.last_sequence, files=("missing.py",))
    CheckpointStore(session.path.with_suffix(".checkpoint.json")).save(checkpoint)
    code = main(["--workspace", str(tmp_path), "run", "--resume", "run", "--fork", "--dry-run", "--json"])
    captured = capsys.readouterr()
    import json
    payload = json.loads(captured.err)
    assert code == 3 and payload["recovery_required"] is True
    assert "Traceback" not in captured.err and "Traceback" not in captured.out
