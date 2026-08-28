"""Regression tests for the v0.0.8 context/skill/hook extension contracts.

These tests intentionally exercise the public service-facing objects rather
than implementation helpers.  They are the acceptance contract for bounded
symbol extraction, filters, extension lifecycle and hook evidence.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from forgecode.context import ContextIndex
from forgecode.hooks import Hook, HookRegistry
from forgecode.security import WorkspaceGuard
from forgecode.skills import SkillExecutor, SkillLoader, SkillRegistry


def test_context_python_ast_and_filters_explain_exclusions(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text(
        "@decorator\nclass Service:\n    async def fetch(self):\n        return 1\n\ndef helper():\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "other.js").write_text("function fetch() { return 3; }\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("fetch documentation\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=must-not-index\n", encoding="utf-8")

    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.build()
    entry = next(item for item in index.entries() if item.path == "src/module.py")
    # AST extraction must include nested/async definitions, with no decorator
    # or comment false positives.
    assert set(entry.symbols) >= {"Service", "fetch", "helper"}

    python = index.search("return", language="Python", line_range=(2, 4), context_lines=0)
    assert python and all(item.language == "Python" and 2 <= item.line <= 4 for item in python)
    assert not index.search("fetch", language="Python", line_range=(1, 1))
    assert index.search("fetch", glob="src/*.py")

    explanation = index.explain()
    assert explanation["included"] >= 3
    excluded = {item["path"]: item["reason"] for item in explanation["excluded"]}
    assert ".env" in excluded and "sensitive" in excluded[".env"]


def test_context_stale_diagnostic_distinguishes_changed_digest(tmp_path: Path):
    target = tmp_path / "stale.py"
    target.write_text("def stable():\n    return 'old'\n", encoding="utf-8")
    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.build()
    target.write_text("def stable():\n    return 'new and changed'\n", encoding="utf-8")
    assert index.search("stable") == ()
    assert any("stale digest" in issue for issue in index.last_search_issues)
    diagnostics = index.diagnostics()
    assert diagnostics["stale"] and diagnostics["stale"][0]["path"] == "stale.py"
    target.unlink()
    missing = index.diagnostics()
    assert missing["stale"] and missing["stale"][0]["reason"] == "missing"


def test_context_combined_filters_are_conjunctive_and_explain_after_reload(tmp_path: Path):
    (tmp_path / "a.py").write_text("def target():\n    needle = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def target():\n    other = 2\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=hidden\n", encoding="utf-8")
    first = ContextIndex(WorkspaceGuard(tmp_path))
    first.build()
    # A keyword and regex must both match the same line; a symbol/language
    # filter must also be satisfied.  A regex-only match in b.py must not leak
    # into a query for ``needle``.
    matches = first.search("needle", regex=r"needle\s*=", symbol="target", language="py")
    assert [item.path for item in matches] == ["a.py"]
    # Symbol selection scopes the file, so body lines are searchable even
    # though the symbol name itself appears only on the declaration line.
    assert first.search("needle", symbol="target")[0].line == 2
    # Explainability must survive a new process loading an existing cache.
    reloaded = ContextIndex(WorkspaceGuard(tmp_path))
    explanation = reloaded.explain()
    assert {item["path"] for item in explanation["files"]} >= {"a.py", "b.py"}
    assert any(item["path"] == ".env" and item["reason"] == "sensitive" for item in explanation["excluded"])


def test_context_cli_human_explain_and_diagnostics(capsys, tmp_path: Path):
    from forgecode.cli import main

    (tmp_path / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "context", "explain"]) == 0
    assert "included=" in capsys.readouterr().out
    assert main(["--workspace", str(tmp_path), "context", "diagnostics"]) == 0
    assert "stale=" in capsys.readouterr().out


def test_context_cli_diagnostics_does_not_refresh_stale_cache(capsys, tmp_path: Path):
    from forgecode.cli import main

    target = tmp_path / "main.py"
    target.write_text("def main():\n    return 'old'\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "context", "index", "--json"]) == 0
    capsys.readouterr()
    target.write_text("def main():\n    return 'new value'\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "context", "diagnostics", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["stale"] and payload["data"]["stale"][0]["reason"] == "digest_changed"


def test_context_explain_includes_binary_and_oversized_reasons(tmp_path: Path):
    (tmp_path / "small.txt").write_text("ok\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00binary")
    (tmp_path / "large.txt").write_bytes(b"x" * 32)
    index = ContextIndex(WorkspaceGuard(tmp_path), max_file_bytes=8)
    index.build()
    explanation = index.explain()
    excluded = {item["path"]: item["reason"] for item in explanation["excluded"]}
    assert "blob.bin" in excluded and "large.txt" in excluded


def test_context_cli_line_range_shorthand(capsys, tmp_path: Path):
    from forgecode.cli import main

    (tmp_path / "main.py").write_text("one\nneedle\nthree\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "context", "search", "needle", "--line-range", "2:2", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["results"] and payload["data"]["results"][0]["line"] == 2


def test_skill_manifest_precedence_migration_cache_and_enable_disable(tmp_path: Path):
    (tmp_path / "skills").mkdir()
    (tmp_path / ".forgecode" / "skills").mkdir(parents=True)
    (tmp_path / "skills" / "hello.md").write_text(
        "---\nschema_version: 0\nid: hello\nversion: 1.0.0\nname: Old\ndescription: old\n---\nold\n",
        encoding="utf-8",
    )
    (tmp_path / ".forgecode" / "skills" / "hello.md").write_text(
        "---\nschema_version: 1\nid: hello\nversion: 2.0.0\nname: New\ndescription: new\n---\nnew\n",
        encoding="utf-8",
    )
    loader = SkillLoader(WorkspaceGuard(tmp_path), directories=("skills", ".forgecode/skills"))
    loaded = loader.discover()
    assert [skill.manifest.id for skill in loaded] == ["hello"]
    # Directory order is an explicit, deterministic precedence contract.
    assert loaded[0].manifest.name == "Old"
    assert any("shadow" in diagnostic for diagnostic in loader.diagnostics)
    assert loaded[0].manifest.schema_version == 1

    registry = SkillRegistry(loaded)
    assert registry.disable("hello") is True
    assert registry.invoke("hello").error == "disabled"
    assert registry.enable("hello") is True
    assert registry.invoke("hello").ok
    assert registry.remove("hello") is True
    assert registry.list() == ()


def test_skill_executable_cwd_and_environment_are_bounded(tmp_path: Path, monkeypatch):
    (tmp_path / "skills").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "echo.py").write_text(
        "import json, os, sys\nargs=json.load(sys.stdin)\nprint(os.getenv('SAFE_VALUE','missing'))\n",
        encoding="utf-8",
    )
    (tmp_path / "skills" / "echo.md").write_text(
        "---\nid: echo\nversion: 1.0.0\nname: Echo\ndescription: echo\nentry: bin/echo.py\nentry_type: python\n"
        "cwd: bin\nenvironment: [SAFE_VALUE]\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SAFE_VALUE", "allowed")
    guard = WorkspaceGuard(tmp_path)
    skill = SkillLoader(guard).discover()[0]
    result = SkillRegistry((skill,)).invoke("echo", executor=SkillExecutor(guard), approved=True)
    assert result.ok and result.output.strip() == "allowed"

    # A symlinked cwd is rejected even when it resolves inside the workspace;
    # callers receive a diagnostic rather than silently executing elsewhere.
    outside = tmp_path.parent / "outside-skill-cwd"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    bad_manifest = skill.manifest.__class__(**{**skill.manifest.to_dict(), "cwd": "linked", "allowed_paths": tuple(skill.manifest.allowed_paths)})
    bad_skill = skill.__class__(bad_manifest, skill.path, skill.content)
    denied = SkillRegistry((bad_skill,)).invoke("echo", executor=SkillExecutor(guard), approved=True)
    assert not denied.ok and "symlink" in (denied.output + str(denied.error)).lower()


def test_skill_executor_bounds_pipe_cleanup_when_child_inherits_output(tmp_path: Path):
    """A detached child must not make executable-skill invocation hang."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "leak.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
        "print('parent-done')\n",
        encoding="utf-8",
    )
    (tmp_path / "skills" / "leak.md").write_text(
        "---\nid: leak\nversion: 1.0.0\nname: Leak\ndescription: pipe leak\n"
        "entry: skills/leak.py\nentry_type: python\ntimeout_seconds: 2\n---\n",
        encoding="utf-8",
    )
    guard = WorkspaceGuard(tmp_path)
    skill = SkillLoader(guard).discover()[0]
    started = time.monotonic()
    result = SkillRegistry((skill,)).invoke("leak", executor=SkillExecutor(guard), approved=True)
    elapsed = time.monotonic() - started
    assert elapsed < 4.0
    assert result.ok and "parent-done" in result.output


def test_skill_executor_input_write_cannot_bypass_timeout(tmp_path: Path):
    """A skill that never reads a large request must still time out promptly."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "ignore-input.py").write_text(
        "import time\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    (tmp_path / "skills" / "ignore-input.md").write_text(
        "---\nid: ignore-input\nversion: 1.0.0\nname: Ignore input\ndescription: bounded input\n"
        "entry: skills/ignore-input.py\nentry_type: python\ntimeout_seconds: 1\n---\n",
        encoding="utf-8",
    )
    guard = WorkspaceGuard(tmp_path)
    skill = SkillLoader(guard).discover()[0]
    started = time.monotonic()
    result = SkillRegistry((skill,)).invoke(
        "ignore-input",
        {"payload": "x" * 39_000},
        executor=SkillExecutor(guard),
        approved=True,
    )
    assert time.monotonic() - started < 4.0
    assert not result.ok and result.error == "SkillError"
    assert "timed out" in result.output.lower()


def test_skill_loader_reports_executable_path_diagnostics_and_is_stable_on_alias(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "bad.md").write_text(
        "---\nid: bad\nversion: 1.0.0\nname: Bad\ndescription: bad\nentry: run.py\nentry_type: python\ncwd: missing\n---\n",
        encoding="utf-8",
    )
    loader = SkillLoader(WorkspaceGuard(tmp_path))
    assert loader.discover() == ()
    assert any("cwd" in item.lower() or "missing" in item.lower() for item in loader.errors)

    # A symlinked discovery directory must be omitted with a bounded error,
    # not abort the entire discovery pass or escape the workspace.
    outside = tmp_path.parent / "forgecode-skills-outside"
    outside.mkdir(exist_ok=True)
    alias = tmp_path / "alias-skills"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    aliased_loader = SkillLoader(WorkspaceGuard(tmp_path), directories=("alias-skills", "skills"))
    assert isinstance(aliased_loader.discover(), tuple)
    assert aliased_loader.errors and all(len(item) < 512 for item in aliased_loader.errors)


def test_hook_correlation_and_policy_are_explicit():
    seen: list[dict] = []
    registry = HookRegistry(
        [
            Hook("observe", "event", lambda payload: seen.append(payload)),
            Hook("closed", "event", lambda payload: (_ for _ in ()).throw(RuntimeError("no")), failure_policy="fail_closed"),
        ]
    )
    issues = registry.emit("event", {"value": "x"}, correlation_id="corr-123")
    assert seen[0]["correlation_id"] == "corr-123"
    assert len(issues) == 1
    issue = issues[0].to_dict()
    assert issue["correlation_id"] == "corr-123"
    assert issue["failure_policy"] == "fail_closed" and issue["blocked"] is True
