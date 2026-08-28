"""Additional adversarial contracts for context, skills, hooks and review.

These tests describe boundaries that are easy to miss when exercising only the
happy-path CLI.  They intentionally use the public classes and keep all
runtime data inside the temporary workspace.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest

from forgecode.context import ContextIndex
from forgecode.hooks import Hook, HookRegistry
from forgecode.review import ReviewBuilder
from forgecode.security import WorkspaceGuard
from forgecode.skills import SkillLoader, SkillRegistry
from forgecode.storage import SessionStore
from forgecode.testing import TestProfile, TestProfileRunner


def test_context_persists_exclusion_reason_and_distinguishes_oversized(tmp_path: Path):
    (tmp_path / "text.txt").write_text("safe\n", encoding="utf-8")
    (tmp_path / "large.txt").write_bytes(b"x" * 64)
    (tmp_path / "blob.bin").write_bytes(b"\x00binary")
    first = ContextIndex(WorkspaceGuard(tmp_path), max_file_bytes=8)
    first.build()
    second = ContextIndex(WorkspaceGuard(tmp_path), max_file_bytes=8)
    explanation = second.explain()
    excluded = {item["path"]: item["reason"] for item in explanation["excluded"]}
    assert "large.txt" in excluded and "oversized" in excluded["large.txt"]
    assert "blob.bin" in excluded and "binary" in excluded["blob.bin"]
    # Explanations survive a new process and are not dependent on a previous
    # in-memory candidate scan.
    assert json.loads(second.path.read_text(encoding="utf-8"))["exclusions"]


def test_context_exposes_structured_stale_search_diagnostic(tmp_path: Path):
    target = tmp_path / "main.py"
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.build()
    target.write_text("def run():\n    return 2\n", encoding="utf-8")
    assert index.search("return") == ()
    assert index.last_search_issues
    assert index.last_search_diagnostics[0]["code"] == "stale_digest"
    assert index.last_search_diagnostics[0]["path"] == "main.py"


def test_context_search_filter_inputs_are_bounded_and_typed(tmp_path: Path):
    (tmp_path / "main.py").write_text("needle\n", encoding="utf-8")
    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.build()
    with pytest.raises(ValueError, match="glob"):
        index.search("needle", glob=123)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="path"):
        index.search("needle", path="x" * 513)
    with pytest.raises(ValueError, match="symbol"):
        index.search("needle", symbol="bad\x00symbol")


def test_skill_registry_state_can_be_saved_and_reloaded(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "hello.md").write_text(
        "---\nid: hello\nversion: 1.0.0\nname: Hello\ndescription: hi\nenabled: true\n---\nhello\n",
        encoding="utf-8",
    )
    guard = WorkspaceGuard(tmp_path)
    loaded = SkillLoader(guard).discover()
    registry = SkillRegistry(loaded)
    assert registry.disable("hello") is True
    state_path = tmp_path / ".forgecode" / "skill-state.json"
    registry.save_state(state_path)
    reloaded = SkillRegistry(SkillLoader(guard).discover())
    reloaded.load_state(state_path)
    assert reloaded.invoke("hello").error == "disabled"
    assert reloaded.remove("hello", persist=True, state_path=state_path) is True
    # Removing runtime state must not delete or mutate the source skill.
    assert (skills / "hello.md").is_file()


def test_skill_same_id_manifest_pair_is_diagnosed_deterministically(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "same.md").write_text(
        "---\nid: same\nversion: 1.0.0\nname: Markdown\ndescription: md\n---\nmd\n",
        encoding="utf-8",
    )
    (skills / "same.skill.json").write_text(
        json.dumps({"id": "same", "version": "2.0.0", "name": "JSON", "description": "json", "content": "json"}),
        encoding="utf-8",
    )
    loader = SkillLoader(WorkspaceGuard(tmp_path))
    result = loader.discover()
    assert len(result) == 1
    assert result[0].manifest.version == "1.0.0"
    assert any("same" in diagnostic and "conflict" in diagnostic for diagnostic in loader.diagnostics)


def test_hook_history_is_redacted_and_correlation_is_available_to_review(tmp_path: Path):
    seen: list[dict] = []
    registry = HookRegistry([Hook("audit", "before_tool", lambda payload: seen.append(payload))])
    issues = registry.emit("before_tool", {"tool": "read_file", "token": "secret-value"}, correlation_id="corr-1")
    assert not issues and seen[0]["correlation_id"] == "corr-1"
    history = registry.history()
    assert history and history[0]["correlation_id"] == "corr-1"
    assert "secret-value" not in json.dumps(history)

    session = SessionStore(tmp_path / ".forgecode" / "sessions" / "run.jsonl", run_id="run-hooks")
    session.append("hook_event", {"event": {"event": "before_tool", "correlation_id": "corr-1", "issues": []}})
    report = ReviewBuilder(WorkspaceGuard(tmp_path)).build(session=session.path)
    assert report.hooks and report.hooks[0]["correlation_id"] == "corr-1"


def test_hook_history_returns_deep_snapshots():
    registry = HookRegistry([Hook("audit", "event", lambda _payload: None)])
    registry.emit("event", {"nested": {"value": "original"}})
    first = registry.history()[0]
    first["payload"]["nested"]["value"] = "mutated"
    first["issues"].append({"hook": "forged"})
    second = registry.history()[0]
    assert second["payload"]["nested"]["value"] == "original"
    assert all(item.get("hook") != "forged" for item in second["issues"])


def test_profile_approval_exception_and_unresolved_termination_never_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    profile = TestProfile("approval", (sys.executable, "-c", "print('must-not-run')"))

    def broken(*_args):
        raise RuntimeError("approval callback failed")

    result = TestProfileRunner(WorkspaceGuard(tmp_path), approval=broken).run(profile)
    assert not result.ok and result.error_code == "approval_error"

    # Make termination report unresolved; a timeout with no proof of process
    # termination is a recovery state, never a passing verification.
    import forgecode.testing as testing

    monkeypatch.setattr(testing, "_terminate_process_tree", lambda _process: "unresolved")
    slow = TestProfile("slow", (sys.executable, "-c", "import time; time.sleep(2)"), timeout_seconds=0.1)
    unresolved = TestProfileRunner(WorkspaceGuard(tmp_path), approval=lambda *_: True).run(slow)
    assert not unresolved.ok
    assert unresolved.error_code in {"termination_unresolved", "timeout"}
    assert unresolved.verification_status != "passed"
