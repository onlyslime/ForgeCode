from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from forgecode.security.workspace import WorkspaceGuard
from forgecode.storage.session import SessionStore
from forgecode.testing import (
    TestProfileError as ProfileError,
    TestProfileLoader as ProfileLoader,
    TestProfileRunner as ProfileRunner,
    workspace_fingerprint,
)


def _write_config(workspace: Path, text: str) -> None:
    directory = workspace / ".forgecode"
    directory.mkdir()
    (directory / "tests.toml").write_text(text, encoding="utf-8")


def _python(*args: str) -> list[str]:
    return [sys.executable, "-c", *args]


def test_loader_defaults_and_explicit_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_config(
        tmp_path,
        """
version = 1
default_profile = "quick"
[profiles.default]
command = ["python", "-m", "pytest", "-q"]
[profiles.quick]
command = ["python", "-m", "pytest", "-q", "tests"]
[profiles.custom]
command = ["python", "-m", "pytest", "-x"]
""",
    )
    profiles = ProfileLoader(tmp_path).load()
    assert profiles.get().name == "quick"
    assert profiles.get("custom").command[-1] == "-x"
    monkeypatch.setenv("FORGECODE_TEST_PROFILE", "custom")
    assert profiles.get("default", env=os.environ).name == "default"
    assert profiles.get(None, env=os.environ).name == "custom"


@pytest.mark.parametrize(
    "fragment",
    [
        'command = "python -m pytest"',
        'cwd = "C:/outside"',
        'cwd = "../escape"',
        'env_allow = ["FORGECODE_API_KEY"]',
        'wat = true',
        'timeout_seconds = 1e999',
    ],
)
def test_loader_rejects_ambiguous_or_unsafe_profile_fields(tmp_path: Path, fragment: str):
    _write_config(tmp_path, f"[profiles.default]\ncommand = [\"python\"]\n{fragment}\n")
    with pytest.raises(ProfileError):
        ProfileLoader(tmp_path).load()


def test_loader_toml_has_bounded_size_and_recursion_errors_are_structured(monkeypatch, tmp_path: Path):
    import tomllib

    directory = tmp_path / ".forgecode"
    directory.mkdir()
    path = directory / "tests.toml"
    path.write_text("#" + ("x" * 1_000_000), encoding="utf-8")
    with pytest.raises(ProfileError, match="safety limit"):
        ProfileLoader(tmp_path).load()

    path.write_text('[profiles.default]\ncommand = ["python"]\n', encoding="utf-8")
    monkeypatch.setattr(tomllib, "load", lambda _stream: (_ for _ in ()).throw(RecursionError("deep TOML")))
    with pytest.raises(ProfileError, match="nesting"):
        ProfileLoader(tmp_path).load()


def test_runner_filters_environment_and_persists_bounded_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORGECODE_TEST_VISIBLE", "yes")
    monkeypatch.setenv("FORGECODE_API_KEY", "do-not-leak")
    _write_config(
        tmp_path,
        """[profiles.default]
command = ["python", "-c", "import os; print(os.getenv('FORGECODE_TEST_VISIBLE')); print(os.getenv('FORGECODE_API_KEY', 'missing'))"]
env_allow = ["FORGECODE_TEST_VISIBLE"]
[profiles.default.output]
stdout_chars = 128
stderr_chars = 128
total_chars = 256
""",
    )
    profiles = ProfileLoader(tmp_path).load()
    session = SessionStore(tmp_path / ".forgecode" / "sessions" / "test.jsonl")
    evidence = ProfileRunner(WorkspaceGuard(tmp_path), approval=lambda *_: True, session=session).run(profiles.get())
    assert evidence.ok
    assert "yes" in evidence.stdout_preview
    assert "missing" in evidence.stdout_preview
    assert "do-not-leak" not in evidence.stdout_preview
    assert len(evidence.stdout_digest) == 64
    events = list(session.read(strict=True))
    assert events[0].kind == "test_profile_result"
    assert events[0].payload["verification_status"] == "passed"


def test_runner_timeout_and_plan_are_never_success(tmp_path: Path):
    profile = ProfileLoader(tmp_path).load().get()
    profile = profile.__class__(profile.name, tuple(_python("import time; time.sleep(5)")), timeout_seconds=0.1, output=profile.output, expected_exit=profile.expected_exit)
    timed = ProfileRunner(WorkspaceGuard(tmp_path), approval=lambda *_: True).run(profile)
    assert not timed.ok and timed.timed_out and timed.verification_status == "timed_out"
    planned = ProfileRunner(WorkspaceGuard(tmp_path), approval=lambda *_: True, mode="plan").run(profile)
    assert not planned.ok and planned.verification_status == "skipped" and planned.error_code == "mode_denied"


def test_runner_denial_and_cancellation_have_evidence(tmp_path: Path):
    profile = ProfileLoader(tmp_path).load().get()
    denied = ProfileRunner(WorkspaceGuard(tmp_path)).run(profile)
    assert not denied.ok and denied.approval == "denied" and denied.error_code == "approval_denied"
    cancelled = ProfileRunner(WorkspaceGuard(tmp_path), approval=lambda *_: True).run(profile, cancel=lambda: True)
    assert not cancelled.ok and cancelled.cancelled and cancelled.verification_status == "cancelled"


def test_unexpected_exit_cannot_pass_and_fingerprint_changes(tmp_path: Path):
    profile = ProfileLoader(tmp_path).load().get()
    profile = profile.__class__(profile.name, tuple(_python("raise SystemExit(9)")), expected_exit=profile.expected_exit)
    before = workspace_fingerprint(WorkspaceGuard(tmp_path))
    result = ProfileRunner(WorkspaceGuard(tmp_path), approval=lambda *_: True).run(profile)
    assert not result.ok and result.verification_status == "failed" and result.error_code == "unexpected_exit"
    (tmp_path / "changed.txt").write_text("changed", encoding="utf-8")
    assert workspace_fingerprint(WorkspaceGuard(tmp_path)) != before


def test_output_quota_and_secret_redaction_are_bounded(tmp_path: Path):
    profile = ProfileLoader(tmp_path).load().get()
    profile = profile.__class__(
        profile.name,
        tuple(_python("print('x' * 400)")),
        output=profile.output.__class__(128, 128, 128),
        expected_exit=profile.expected_exit,
    )
    result = ProfileRunner(WorkspaceGuard(tmp_path), approval=lambda *_: True, secrets=("x",)).run(profile)
    assert result.ok and result.truncated
    assert len(result.stdout_preview) <= 128
