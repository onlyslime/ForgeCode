from pathlib import Path

from forgecode.context import RepositoryMapBuilder
from forgecode.security import WorkspaceGuard
from forgecode.tools import DenyAllApproval, ToolContext, build_default_registry


def test_repository_map_is_bounded_sorted_and_ignores_sensitive_binary(tmp_path: Path):
    (tmp_path / "z.py").write_text("class Z:\n    pass\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=should-not-appear", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00binary")
    repository = RepositoryMapBuilder(WorkspaceGuard(tmp_path)).build()
    paths = [item.path for item in repository.snapshot.files]
    assert paths == sorted(paths)
    assert "a.py" in paths and "z.py" in paths
    assert ".env" not in paths and "image.png" not in paths
    assert repository.snapshot.language_counts["Python"] == 2


def test_repository_context_plan_prioritizes_task_and_reports_omissions(tmp_path: Path):
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("misc", encoding="utf-8")
    repository = RepositoryMapBuilder(WorkspaceGuard(tmp_path), max_file_bytes=10_000).build()
    plan = repository.plan_context("fix calculator add", budget_chars=300)
    assert plan.selected_paths
    assert plan.selected_paths[0] == "calculator.py"
    assert plan.omitted >= 0
    assert plan.budget_chars == 300


def test_repository_map_tool_is_read_only(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)
    result = registry.execute("repository_map", {"task": "inspect"}, ToolContext(guard, DenyAllApproval(), mode="plan"))
    assert result.ok
    assert result.metadata["snapshot"]["root"] == "."
    assert not list(tmp_path.iterdir())


def test_repository_map_honors_gitignore(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("ignored.txt\ncache/\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "a.py").write_text("def hidden(): pass", encoding="utf-8")
    (tmp_path / "visible.py").write_text("def shown(): pass", encoding="utf-8")
    repository = RepositoryMapBuilder(WorkspaceGuard(tmp_path)).build()
    assert [item.path for item in repository.snapshot.files] == [".gitignore", "visible.py"]


def test_repository_map_and_references_share_negation_and_private_exclusions(tmp_path: Path):
    from forgecode.references import ReferenceResolver

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.py").write_text("keep\n", encoding="utf-8")
    (tmp_path / "src" / "hidden.py").write_text("hidden\n", encoding="utf-8")
    (tmp_path / ".forgecode" / "sessions").mkdir(parents=True)
    (tmp_path / ".forgecode" / "sessions" / "private.py").write_text("secret\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("src/*.py\n!src/keep.py\n!.forgecode/sessions/private.py\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)

    mapped = {item.path for item in RepositoryMapBuilder(guard).build().snapshot.files}
    referenced = {item.path for item in ReferenceResolver(guard).resolve(["src"]).items}
    assert "src/keep.py" in mapped and "src/keep.py" in referenced
    assert "src/hidden.py" not in mapped and "src/hidden.py" not in referenced
    assert ".forgecode/sessions/private.py" not in mapped
    assert not ReferenceResolver(guard).resolve([".forgecode/sessions/private.py"]).items


def test_repository_map_omits_file_changed_during_read(monkeypatch, tmp_path: Path):
    target = tmp_path / "changing.py"
    target.write_text("before\n", encoding="utf-8")
    original = Path.read_bytes

    def changing_read(path):
        data = original(path)
        if path == target:
            target.write_text("changed with a different size\n", encoding="utf-8")
        return data

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    repository = RepositoryMapBuilder(WorkspaceGuard(tmp_path)).build()
    assert "changing.py" not in {item.path for item in repository.snapshot.files}
    assert any("changing.py" in error for error in repository.snapshot.errors)
