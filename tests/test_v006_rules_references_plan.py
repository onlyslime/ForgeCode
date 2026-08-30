from pathlib import Path

import pytest

from forgecode.plan import PlanError, PlanItem, TaskPlan
from forgecode.references import ReferenceResolver, parse_references
from forgecode.rules import RuleEngine
from forgecode.security import WorkspaceGuard


def test_scoped_rules_have_stable_sources_digest_scope_and_conflict(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Always run tests.\r\nRequire approval.\r\n", encoding="utf-8", newline="")
    source = tmp_path / "src"
    source.mkdir()
    (source / "AGENTS.md").write_text("Never run tests.\nAuto approve.\n", encoding="utf-8")
    (source / "main.py").write_text("print('你好')\n", encoding="utf-8")
    rules = RuleEngine(WorkspaceGuard(tmp_path)).discover(["src/main.py"])
    assert [item.path for item in rules.sources] == ["AGENTS.md", "src/AGENTS.md"]
    assert [item.scope for item in rules.sources] == [".", "src"]
    assert rules.sources[1].priority > rules.sources[0].priority
    assert all(len(item.digest) == 64 for item in rules.sources)
    assert len(rules.conflicts) == 2
    assert RuleEngine(WorkspaceGuard(tmp_path)).discover(["src/main.py"]).fingerprint == rules.fingerprint


def test_rules_are_bounded_and_private_directories_are_not_sources(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("x" * 1_000, encoding="utf-8")
    (tmp_path / "docs" / "goals").mkdir(parents=True)
    (tmp_path / "docs" / "goals" / "AGENTS.md").write_text("secret", encoding="utf-8")
    rules = RuleEngine(WorkspaceGuard(tmp_path), max_file_chars=128, max_total_chars=256).discover(["docs/goals"])
    assert rules.sources[0].truncated
    assert "secret" not in rules.text
    assert any(item.code == "sensitive_omitted" for item in rules.diagnostics)


def test_rules_share_private_and_gitignore_policy_with_context_sources(tmp_path: Path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "AGENTS.md").write_text("do not expose", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "AGENTS.md").write_text("ignored rule", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("vendor/\n", encoding="utf-8")
    rules = RuleEngine(WorkspaceGuard(tmp_path)).discover([".venv", "vendor"])
    assert not rules.sources
    assert all("do not expose" not in rules.text and "ignored rule" not in rules.text for _ in [0])


def test_explicit_references_resolve_file_directory_and_ignore_email_sensitive(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "src" / "space name.py").write_text("print('space')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    assert parse_references('mail user@example.com @src/a.py @"src/space name.py" \\@literal') == ("src/a.py", "src/space name.py")
    resolved = ReferenceResolver(guard).resolve(["src", ".env", "src/a.py"])
    assert {item.path for item in resolved.items} == {"src/a.py", "src/space name.py"}
    assert all(len(item.digest or "") == 64 for item in resolved.items)
    assert any(item.code == "sensitive_omitted" for item in resolved.diagnostics)


def test_references_glob_and_directory_obey_gitignore_for_direct_and_expanded_paths(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.py").write_text("keep\n", encoding="utf-8")
    (tmp_path / "src" / "ignored.py").write_text("must-not-enter-context\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("src/*.py\n!src/keep.py\nprivate/\n", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "secret.py").write_text("private\n", encoding="utf-8")
    resolver = ReferenceResolver(WorkspaceGuard(tmp_path))

    directory = resolver.resolve(["src"])
    assert [item.path for item in directory.items] == ["src/keep.py"]
    globbed = resolver.resolve(["src/*.py"])
    assert [item.path for item in globbed.items] == ["src/keep.py"]
    direct = resolver.resolve(["src/ignored.py", "private/secret.py"])
    assert not direct.items
    assert all(item.code == "ignored_omitted" for item in direct.diagnostics)


def test_reference_glob_rejects_parent_traversal(tmp_path: Path):
    resolution = ReferenceResolver(WorkspaceGuard(tmp_path)).resolve(["../*.py"])
    assert not resolution.items
    assert resolution.diagnostics[0].code == "path_rejected"


def test_rules_and_references_report_read_change_as_fatal(monkeypatch, tmp_path: Path):
    from forgecode.references import ReferenceResolver

    rule = tmp_path / "AGENTS.md"
    rule.write_text("Run tests.\n", encoding="utf-8")
    target = tmp_path / "target.py"
    target.write_text("print(1)\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes
    changed = {"rule": False, "target": False}

    def read_bytes(path):
        data = original_read_bytes(path)
        if path == rule and not changed["rule"]:
            changed["rule"] = True
            rule.write_text("Changed while reading.\n", encoding="utf-8")
        if path == target and not changed["target"]:
            changed["target"] = True
            target.write_text("changed while reading\n", encoding="utf-8")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    rules = RuleEngine(WorkspaceGuard(tmp_path)).discover()
    assert any(item.code == "read_changed" and item.severity == "error" for item in rules.diagnostics)
    references = ReferenceResolver(WorkspaceGuard(tmp_path)).resolve(["target.py"])
    assert references.has_errors and any(item.code == "read_changed" for item in references.diagnostics)


def test_git_virtual_references_are_read_only_and_bounded(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    resolved = ReferenceResolver(WorkspaceGuard(tmp_path)).resolve(["git:status", "git:diff", "git:unknown"])
    assert [item.reference for item in resolved.items] == ["git:status", "git:diff"]
    assert "a.txt" in resolved.items[0].content
    assert any(item.code == "git_error" for item in resolved.diagnostics)


def test_structured_plan_validates_dag_revision_stale_and_evidence():
    plan = TaskPlan(task="fix", items=(PlanItem("inspect", "Inspect"), PlanItem("edit", "Edit", dependencies=("inspect",), acceptance_criteria=("tests pass",))))
    plan.validate()
    acting = plan.approve_for_act()
    assert acting.approved and acting.mode == "act" and acting.revision == 2
    progressed = acting.update_status("inspect", "in_progress").update_status("inspect", "completed", evidence={"event": 9})
    assert progressed.items[0].evidence[0]["event"] == 9
    stale = progressed.mark_stale_if_changed(rules_fingerprint="changed")
    assert stale.stale and not stale.approved
    with pytest.raises(PlanError, match="stale"):
        stale.approve_for_act()
    with pytest.raises(PlanError, match="approval reason"):
        plan.approve_for_act(reason=None)


def test_structured_plan_rejects_cycles_unknown_dependencies_and_bad_transitions():
    with pytest.raises(PlanError, match="cycle"):
        TaskPlan(task="x", items=(PlanItem("a", "A", dependencies=("b",)), PlanItem("b", "B", dependencies=("a",)))).validate()
    with pytest.raises(PlanError, match="unknown"):
        TaskPlan(task="x", items=(PlanItem("a", "A", dependencies=("missing",)),)).validate()
    plan = TaskPlan(task="x", items=(PlanItem("a", "A"),)).update_status("a", "in_progress").update_status("a", "completed", evidence={"ok": True})
    with pytest.raises(PlanError, match="invalid status transition"):
        plan.update_status("a", "in_progress")


def test_structured_plan_rejects_unknown_fields_nonfinite_and_unbounded_collections():
    with pytest.raises(PlanError, match="unknown plan fields"):
        TaskPlan.from_dict({"task": "x", "items": [], "permission": "auto-approve"})
    with pytest.raises(PlanError, match="unknown plan item fields"):
        TaskPlan.from_dict({"task": "x", "items": [{"id": "a", "title": "A", "execute": True}]})
    with pytest.raises(PlanError, match="evidence is invalid"):
        TaskPlan(task="x", items=(PlanItem("a", "A", evidence=({"score": float("nan")},)),)).validate()
    with pytest.raises(PlanError, match="expected_files"):
        TaskPlan(task="x", items=(PlanItem("a", "A", expected_files=tuple(str(index) for index in range(257))),)).validate()


def test_plan_cannot_claim_completion_without_evidence():
    plan = TaskPlan(task="x", items=(PlanItem("a", "A"),)).update_status("a", "in_progress")
    with pytest.raises(PlanError, match="requires evidence"):
        plan.update_status("a", "completed")


def test_plan_cli_discovers_nested_rules_for_explicit_reference(capsys, tmp_path: Path):
    from forgecode.application.commands import main

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("Nested scope.\n", encoding="utf-8")
    (tmp_path / "src" / "target.py").write_text("print(1)\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "plan", "fix", "@src/target.py", "--json"]) == 0
    payload = __import__("json").loads(capsys.readouterr().out)
    assert [source["path"] for source in payload["rules"]["sources"]] == ["src/AGENTS.md"]
