import json
from pathlib import Path

import pytest

from forgecode.cli import main
from forgecode.context import ContextIndex, ContextIndexError
from forgecode.security import WorkspaceGuard
from forgecode.skills import SkillError, SkillLoader, SkillRegistry
from forgecode.hooks import Hook, HookRegistry
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry
from forgecode.models import DemoProvider, OpenAICompatibleProvider, ProviderError
from forgecode.storage import TransactionError, TransactionStore


def test_context_index_build_search_incremental_and_sensitive_exclusion(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def greet(name):\n    return 'hello ' + name\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("greet appears here\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=do-not-index\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00secret")
    index = ContextIndex(WorkspaceGuard(tmp_path))
    first = index.build()
    assert first.files == 3
    assert first.added == 3
    result = index.search("greet")
    assert [item.path for item in result] == ["notes.txt", "src/main.py"]
    assert all("do-not-index" not in item.snippet for item in result)
    second_cold = index.ensure()
    assert second_cold.added == 0 and second_cold.updated == 0
    before = index.show()["fingerprint"]
    (tmp_path / "src" / "main.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    second = index.ensure()
    assert second.updated >= 1 and second.fingerprint != before
    assert index.search(symbol="greet") and index.search(symbol="greet")[0].digest != before
    (tmp_path / "src" / "main.py").write_text("changed\n", encoding="utf-8")
    assert all(item.path == "notes.txt" for item in index.search("greet")) and index.last_search_issues


def test_context_index_visits_ignored_directory_for_explicit_negation(tmp_path: Path):
    (tmp_path / "generated").mkdir()
    (tmp_path / ".gitignore").write_text("generated/\n!generated/keep.txt\n", encoding="utf-8")
    (tmp_path / "generated" / "keep.txt").write_text("reinclude-me\n", encoding="utf-8")
    (tmp_path / "generated" / "drop.txt").write_text("do-not-index\n", encoding="utf-8")
    entries = ContextIndex(WorkspaceGuard(tmp_path)).build()
    indexed = {item.path for item in ContextIndex(WorkspaceGuard(tmp_path)).entries()}
    assert entries.files == 2 and indexed == {".gitignore", "generated/keep.txt"}


def test_context_index_excludes_runtime_log_jsonl_and_backup_suffixes(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("safe context\n", encoding="utf-8")
    for name in ("trace.log", "session.jsonl", "source.bak", "copy.orig"):
        (tmp_path / name).write_text("TOKEN=do-not-expose\n", encoding="utf-8")
    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.build()
    assert {item.path for item in index.entries()} == {"notes.txt"}


def test_context_index_corruption_rebuilds_and_clear_is_safe(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    index = ContextIndex(guard)
    index.build()
    index.path.write_text("{broken", encoding="utf-8")
    report = index.ensure()
    assert report.rebuilt is True
    assert index.clear() is True
    assert not index.path.exists()


def test_context_index_records_real_digest_for_binary_and_oversized_files(tmp_path: Path):
    binary = b"\x00binary payload"
    large = b"x" * 32
    (tmp_path / "image.bin").write_bytes(binary)
    (tmp_path / "large.txt").write_bytes(large)
    report = ContextIndex(WorkspaceGuard(tmp_path), max_file_bytes=8).build()
    entries = {item.path: item for item in ContextIndex(WorkspaceGuard(tmp_path)).entries()}
    import hashlib

    assert report.files == 2
    assert entries["image.bin"].digest == hashlib.sha256(binary).hexdigest()
    assert entries["large.txt"].digest == hashlib.sha256(large).hexdigest()


def test_context_index_rejects_unsafe_index_entries(tmp_path: Path):
    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.path.parent.mkdir(parents=True)
    index.path.write_text(json.dumps({"schema_version": 1, "fingerprint": "0" * 64, "files": [{"path": "../escape", "size": 1, "mtime_ns": 1, "digest": "0" * 64, "language": None, "lines": 1, "readable": True, "sensitive": False, "ignored": False, "binary": False, "symbols": []}]}), encoding="utf-8")
    with pytest.raises(ContextIndexError):
        index._load()


def test_context_index_rejects_nonfinite_json_values(tmp_path: Path):
    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.path.parent.mkdir(parents=True)
    index.path.write_text('{"schema_version":1,"fingerprint":NaN,"files":[]}', encoding="utf-8")
    with pytest.raises(ContextIndexError, match="non-finite"):
        index._load()


def test_transaction_manifest_rejects_nonfinite_json_values(tmp_path: Path):
    store = TransactionStore(WorkspaceGuard(tmp_path))
    store.manifest_dir.mkdir(parents=True)
    path = store.manifest_dir / "bad.json"
    path.write_text('{"schema_version":1,"transaction_id":"bad","run_id":"run","created_at":"2026-01-01T00:00:00+00:00","tool":"x","state":"committed","operations":[],"verification":{"score":NaN}}', encoding="utf-8")
    with pytest.raises(TransactionError, match="non-finite"):
        store.load("bad")


def test_skill_loader_manifest_validation_and_readonly_invocation(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "explain.md").write_text("---\nid: explain\nversion: 1.0.0\nname: Explain\ndescription: Explain code\n---\nUse concise examples.\n", encoding="utf-8")
    (skills / "unsafe.md").write_text("---\nid: unsafe\nversion: 1.0.0\nname: Unsafe\ndescription: Bad\nside_effect: write\nentry_type: python\nentry: run.py\n---\n", encoding="utf-8")
    loaded = SkillLoader(WorkspaceGuard(tmp_path)).discover()
    registry = SkillRegistry(loaded)
    invocation = registry.invoke("explain")
    assert invocation.ok and "concise" in invocation.output
    denied = registry.invoke("unsafe")
    assert not denied.ok and denied.error == "approval_required"
    assert any("unsafe" in skill_id for skill_id in {item.manifest.id for item in loaded})


def test_skill_loader_rejects_unknown_fields_and_duplicate_ids(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "one.md").write_text("---\nid: duplicate\nversion: 1.0.0\nname: One\ndescription: One\nunknown: true\n---\n", encoding="utf-8")
    (skills / "two.md").write_text("---\nid: duplicate\nversion: 1.0.0\nname: Two\ndescription: Two\n---\n", encoding="utf-8")
    loader = SkillLoader(WorkspaceGuard(tmp_path))
    assert len(loader.discover()) == 1
    assert loader.errors and any("unknown" in error for error in loader.errors)


def test_skill_loader_rejects_ambiguous_markdown_permissions_and_schema(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "write.md").write_text("---\nid: write\nversion: 1.0.0\nname: Write\ndescription: bad\nside_effect: write\n---\n", encoding="utf-8")
    (skills / "schema.md").write_text("---\nid: schema\nversion: 1.0.0\nname: Schema\ndescription: bad\ninput_schema: {\"type\":\"object\",\"wat\":true}\n---\n", encoding="utf-8")
    loader = SkillLoader(WorkspaceGuard(tmp_path))
    assert loader.discover() == ()
    assert any("Markdown skills" in error for error in loader.errors)
    assert any("input_schema" in error for error in loader.errors)


def test_cli_context_and_skills_json_contract(capsys, tmp_path: Path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "hello.md").write_text("---\nid: hello\nversion: 1.0.0\nname: Hello\ndescription: greeting\n---\nhello\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "skills", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["skills"][0]["manifest"]["id"] == "hello"
    assert main(["--workspace", str(tmp_path), "skills", "run", "hello", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["--workspace", str(tmp_path), "context", "index", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["files"] >= 1
    assert main(["--workspace", str(tmp_path), "context", "search", "hello", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["count"] >= 1 and all(item["path"].endswith("hello.md") for item in result["results"])


def test_lifecycle_hooks_observe_and_fail_closed_without_bypassing_plan(tmp_path: Path):
    observed: list[str] = []
    hooks = HookRegistry([Hook("audit", "*", lambda event: observed.append(event["tool"]))])
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)
    result = registry.execute("read_file", {"path": "missing.txt"}, ToolContext(guard, AllowAllApproval(), mode="plan", hooks=hooks))
    assert not result.ok and observed == ["read_file", "read_file"]

    def reject(_event):
        raise RuntimeError("reject")

    blocking = HookRegistry([Hook("reject", "before_tool", reject, failure_policy="fail_closed")])
    blocked = registry.execute("read_file", {"path": "missing.txt"}, ToolContext(guard, AllowAllApproval(), mode="plan", hooks=blocking))
    assert not blocked.ok and blocked.metadata["error"] == "hook_blocked"


def test_lifecycle_hook_recursion_is_blocked(tmp_path: Path):
    holder: dict[str, HookRegistry] = {}

    def recurse(_event):
        assert holder["registry"].emit("before_tool", {"tool": "nested"})[0].blocked

    holder["registry"] = HookRegistry([Hook("recursive", "before_tool", recurse, failure_policy="fail_closed")])
    issues = holder["registry"].emit("before_tool", {"tool": "read_file"})
    assert issues == ()


def test_lifecycle_hook_timeout_returns_with_fail_closed_issue():
    import time

    def slow(_event):
        time.sleep(0.15)

    registry = HookRegistry([Hook("slow", "before_model", slow, failure_policy="fail_closed", timeout_seconds=0.01)])
    started = time.monotonic()
    issues = registry.emit("before_model", {"step": 1})
    elapsed = time.monotonic() - started
    assert elapsed < 0.1
    assert issues and issues[0].blocked and issues[0].error == "TimeoutError"


def test_lifecycle_after_hook_failure_stops_agent_after_real_result(tmp_path: Path):
    class OneReadProvider:
        calls = 0

        async def complete(self, messages, tools):
            from forgecode.models import Message, ModelResponse, ToolCall

            self.calls += 1
            if self.calls == 1:
                return ModelResponse(Message("assistant", tool_calls=(ToolCall("read-1", "read_file", {"path": "file.txt"}),)))
            return ModelResponse(Message("assistant", "done"))

    from forgecode.agent import AgentLoop, AgentConfig

    (tmp_path / "file.txt").write_text("ok", encoding="utf-8")

    def reject(_event):
        raise RuntimeError("audit unavailable")

    hooks = HookRegistry([Hook("after", "after_tool", reject, failure_policy="fail_closed")])
    registry = build_default_registry(WorkspaceGuard(tmp_path))
    loop = AgentLoop(OneReadProvider(), registry, ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval(), hooks=hooks), config=AgentConfig(max_steps=3))
    result = __import__("asyncio").run(loop.run("read"))
    assert result.stopped_reason == "recovery_conflict"
    assert result.error and "hook_failed_after_effect" in result.error


def test_provider_capabilities_and_health_are_offline_and_bounded():
    demo = DemoProvider()
    assert demo.health()["configured"] is True
    provider = OpenAICompatibleProvider(api_key="fake-key", base_url="https://example.test/v1", model="demo")
    health = provider.health()
    assert health["configured"] is True and health["capabilities"]["tool_calling"] is True
    assert "fake-key" not in str(health)


def test_provider_sse_rejects_nonfinite_json_frame():
    from forgecode.models.openai_compatible import _sse_json_events

    with pytest.raises(ProviderError, match="malformed SSE JSON"):
        _sse_json_events([b'data: {"choices":[],"usage":{"total_tokens":NaN}}\n', b"data: [DONE]\n"])


def test_provider_transport_timeout_is_bounded_and_retryable():
    import time

    class SlowTransport:
        def post_json(self, *_args):
            time.sleep(0.2)
            return 200, b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"late"}}]}'

    provider = OpenAICompatibleProvider(
        api_key="fake-key", base_url="https://example.test/v1", model="m",
        transport=SlowTransport(), timeout=0.01, max_retries=0,
    )
    started = time.monotonic()
    with pytest.raises(ProviderError, match="request failed"):
        __import__("asyncio").run(provider.complete([], []))
    assert time.monotonic() - started < 0.15


def test_interactive_skills_command_is_local_and_json_safe(capsys, monkeypatch, tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "hello.md").write_text("---\nid: hello\nversion: 1.0.0\nname: Hello\ndescription: greeting\n---\nhello\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", iter(["/skills", "/skill hello", "/quit"]))
    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--json"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    payloads = [line.get("payload") for line in lines if line.get("type") == "interactive_result"]
    assert any(isinstance(payload, dict) and payload.get("skills") for payload in payloads)
    assert any(isinstance(payload, dict) and payload.get("ok") is True for payload in payloads)


def test_cli_skill_input_is_validated_and_passed_to_executor(capsys, tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "echo.py").write_text("import json,sys\nprint(json.load(sys.stdin)['value'])", encoding="utf-8")
    (skills / "echo.md").write_text(
        '---\nid: echo\nversion: 1.0.0\nname: Echo\ndescription: echo\nentry: skills/echo.py\n'
        'entry_type: python\nside_effect: read_only\ninput_schema: {"type":"object","properties":{"value":{"type":"string"}},"required":["value"]}\n---\n',
        encoding="utf-8",
    )
    assert main(["--workspace", str(tmp_path), "skills", "run", "echo", "--input", '{"value":"hello"}', "--approve", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["output"].strip() == "hello"
    assert main(["--workspace", str(tmp_path), "skills", "run", "echo", "--input", "[]", "--approve", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_interactive_side_effect_skill_requires_act_and_explicit_approval(capsys, monkeypatch, tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "write.py").write_text("print('ran')", encoding="utf-8")
    (skills / "write.md").write_text(
        '---\nid: write\nversion: 1.0.0\nname: Write\ndescription: write\nentry: skills/write.py\n'
        'entry_type: python\nside_effect: write\napproval: always\n---\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin", iter(["/skill write", "/skill write --approve", "/quit"]))
    # The first invocation is denied by approval; the second prompt is also
    # denied by the interactive approval callback's default EOF-safe input.
    assert main(["--workspace", str(tmp_path), "chat", "--demo", "--json"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    payloads = [line.get("payload") for line in lines if line.get("type") == "interactive_result"]
    assert any(isinstance(item, dict) and item.get("error") in {"approval_required", "mode_denied"} for item in payloads)


def test_provider_health_cli_never_requires_key_or_network(capsys, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FORGECODE_API_KEY", raising=False)
    monkeypatch.delenv("FORGECODE_MODEL", raising=False)
    assert main(["--workspace", str(tmp_path), "provider", "health", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network_request"] is False and payload["configured"] is False


def test_run_json_provider_configuration_failure_is_machine_readable(capsys, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FORGECODE_API_KEY", raising=False)
    monkeypatch.delenv("FORGECODE_MODEL", raising=False)
    assert main(["--workspace", str(tmp_path), "run", "offline request", "--auto-approve", "--json"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False and payload["error"] == "run_failed"
    assert "API_KEY" in payload["message"]


def test_run_json_plan_approval_failure_is_machine_readable(capsys, tmp_path: Path):
    config = tmp_path / ".forgecode" / "config.toml"
    config.parent.mkdir()
    config.write_text('approval = "deny"\nmodel = "demo"\n', encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "run", "offline request", "--json"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False and payload["error"] == "approval_denied"
    assert "approval" not in captured.err.lower()


def test_context_and_skills_jsonl_stdout_is_line_parseable(capsys, tmp_path: Path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "one.md").write_text("---\nid: one\nversion: 1.0.0\nname: One\ndescription: one\n---\ntext\n", encoding="utf-8")
    (tmp_path / "one.py").write_text("print('one')\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "skills", "list", "--jsonl"]) == 0
    skill_lines = capsys.readouterr().out.splitlines()
    assert skill_lines and all(isinstance(json.loads(line), dict) for line in skill_lines)
    assert main(["--workspace", str(tmp_path), "context", "search", "one", "--jsonl"]) == 0
    context_lines = capsys.readouterr().out.splitlines()
    assert context_lines and json.loads(context_lines[-1])["type"] == "context_summary"


def test_python_skill_requires_approval_and_runs_with_filtered_environment(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "echo.py").write_text("import json, os, sys\nargs=json.load(sys.stdin)\nprint(args['value'])\nprint(os.getenv('FORGECODE_API_KEY','missing'))\n", encoding="utf-8")
    (skills / "echo.md").write_text("---\nid: echo\nversion: 1.0.0\nname: Echo\ndescription: echo\nentry: skills/echo.py\nentry_type: python\nside_effect: command\napproval: always\ninput_schema: {\"type\": \"object\", \"properties\": {\"value\": {\"type\": \"string\"}}, \"required\": [\"value\"]}\n---\n", encoding="utf-8")
    registry = SkillRegistry(SkillLoader(WorkspaceGuard(tmp_path)).discover())
    executor = __import__("forgecode.skills", fromlist=["SkillExecutor"]).SkillExecutor(WorkspaceGuard(tmp_path))
    denied = registry.invoke("echo", {"value": "x"}, executor=executor)
    assert not denied.ok and denied.error == "approval_required"
    ok = registry.invoke("echo", {"value": "x"}, executor=executor, approved=True)
    assert ok.ok and "x" in ok.output and "missing" in ok.output


def test_executable_skill_requires_approval_even_when_marked_read_only(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "x.py").write_text("print('executed')", encoding="utf-8")
    (skills / "x.md").write_text(
        "---\nid: x\nversion: 1.0.0\nname: X\ndescription: x\n"
        "entry: skills/x.py\nentry_type: python\nside_effect: read_only\n---\n",
        encoding="utf-8",
    )
    guard = WorkspaceGuard(tmp_path)
    registry = SkillRegistry(SkillLoader(guard).discover())
    executor = __import__("forgecode.skills", fromlist=["SkillExecutor"]).SkillExecutor(guard)
    denied = registry.invoke("x", executor=executor)
    assert not denied.ok and denied.error == "approval_required"
    approved = registry.invoke("x", executor=executor, approved=True)
    assert approved.ok and approved.output.strip() == "executed"


def test_skill_accepts_single_character_id_and_rejects_non_object_arguments(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "x.md").write_text(
        "---\nid: x\nversion: 1.0.0\nname: X\ndescription: x\n---\ntext\n",
        encoding="utf-8",
    )
    loaded = SkillLoader(WorkspaceGuard(tmp_path)).discover()
    assert [item.manifest.id for item in loaded] == ["x"]
    result = SkillRegistry(loaded).invoke("x", [])  # type: ignore[arg-type]
    assert not result.ok and result.error == "invalid_arguments"


def test_skill_invocation_enforces_additional_properties_nested_items_and_enum(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "typed.md").write_text(
        '---\nid: typed\nversion: 1.0.0\nname: Typed\ndescription: typed\n'
        'input_schema: {"type":"object","additionalProperties":false,"properties":{"mode":{"type":"string","enum":["safe"]},"items":{"type":"array","items":{"type":"integer"}}},"required":["mode"]}\n---\n',
        encoding="utf-8",
    )
    registry = SkillRegistry(SkillLoader(WorkspaceGuard(tmp_path)).discover())
    assert registry.invoke("typed", {"mode": "safe", "items": [1, 2]}).ok
    assert registry.invoke("typed", {"mode": "unsafe"}).error == "invalid_arguments"
    assert registry.invoke("typed", {"mode": "safe", "unknown": 1}).error == "invalid_arguments"
    assert registry.invoke("typed", {"mode": "safe", "items": ["1"]}).error == "invalid_arguments"


def test_executable_skill_allowed_paths_are_enforced(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "echo.py").write_text("print('ok')", encoding="utf-8")
    (skills / "echo.md").write_text("---\nid: echo\nversion: 1.0.0\nname: Echo\ndescription: echo\nentry: skills/echo.py\nentry_type: python\nallowed_paths: [\"other/**\"]\n---\n", encoding="utf-8")
    registry = SkillRegistry(SkillLoader(WorkspaceGuard(tmp_path)).discover())
    executor = __import__("forgecode.skills", fromlist=["SkillExecutor"]).SkillExecutor(WorkspaceGuard(tmp_path))
    result = registry.invoke("echo", executor=executor, approved=True)
    assert not result.ok and result.error == "SkillError"


def test_run_records_context_index_and_hook_events(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["succeeded"] is True
    session_files = list((tmp_path / ".forgecode" / "sessions").glob("*.jsonl"))
    events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    kinds = {event["kind"] for event in events}
    assert "context_index" in kinds and "hook_event" in kinds


def test_review_aggregates_session_plan_references_and_checks(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--json"]) == 0
    capsys.readouterr()
    assert main(["--workspace", str(tmp_path), "review", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"]["session"] and payload["review"]["plan"] and payload["review"]["checks"]
    assert payload["review"]["audit_complete"] is True
