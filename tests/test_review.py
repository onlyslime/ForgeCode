"""Focused evidence-driven review/security checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgecode.review import (
    ReviewArtifactError,
    ReviewBuilder,
    ReviewError,
    export_review,
    import_review,
    run_security_checks,
)
from forgecode.security import WorkspaceGuard
from forgecode.storage import SessionStore, TransactionStore


def _transaction(workspace: Path, *, content: bytes = b"print('ok')\n", path: str = "src/main.py"):
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    before = target.read_bytes() if target.exists() else None
    import hashlib

    after_hash = hashlib.sha256(content).hexdigest()
    operation = "update" if before is not None else "create"
    store = TransactionStore(WorkspaceGuard(workspace))
    manifest = store.prepare(
        transaction_id="tx-review",
        run_id="run-review",
        tool="test",
        operations=[{"path": path, "operation": operation, "after_sha256": after_hash, "before_bytes": len(before or b""), "after_bytes": len(content)}],
        before_bytes={path: before},
        preview="- old\n+ new",
    )
    target.write_bytes(content)
    return store.commit(manifest.transaction_id)


def test_security_checks_detect_secrets_commands_forbidden_paths_and_syntax(tmp_path: Path):
    (tmp_path / ".env").write_text("TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    session = SessionStore(tmp_path / ".forgecode" / "sessions" / "run.jsonl", run_id="run-review")
    session.append("command_result", {"command": "git reset --hard HEAD", "ok": False, "exit_code": 1})
    checks, findings = run_security_checks(guard, events=session.read(), changed_paths=("bad.py",), max_files=32)
    by_id = {item.check_id: item for item in checks}
    assert by_id["secrets"].status == "fail"
    assert by_id["suspicious_commands"].status == "fail"
    assert by_id["syntax"].status == "fail"
    assert any(item.path == ".env" for item in findings)
    assert all("ghp_" not in item.message for item in findings)

    checks, findings = run_security_checks(guard, operations=(), max_files=32)
    assert dict((item.check_id, item.status) for item in checks)["forbidden_paths"] == "skipped"


def test_syntax_check_scans_workspace_when_no_transaction_paths(tmp_path: Path):
    """A standalone review must not silently skip broken Python files."""
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    checks, findings = run_security_checks(WorkspaceGuard(tmp_path), max_files=32)
    syntax = next(item for item in checks if item.check_id == "syntax")
    assert syntax.status == "fail"
    assert any(item.path == "broken.py" and item.check_id == "syntax" for item in findings)


def test_review_report_joins_transaction_session_and_conflict_state(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("before\n", encoding="utf-8")
    store = SessionStore(tmp_path / ".forgecode" / "sessions" / "run.jsonl", run_id="run-review")
    store.append("plan_created", {"plan": {"plan_id": "plan-1", "items": []}})
    manifest = _transaction(tmp_path, content=b"print('changed')\n", path="src/main.py")
    store.append("transaction_committed", {"transaction_id": manifest.transaction_id, "operations": ["src/main.py"]})
    report = ReviewBuilder(WorkspaceGuard(tmp_path)).build()
    payload = report.to_dict()
    assert payload["schema_version"] == 1
    assert payload["transactions"] and payload["hunks"]
    assert payload["session"]["events"] >= 2
    assert payload["plan"]["plan"]["plan_id"] == "plan-1"
    assert payload["exit_code"] == 0
    assert payload["report_sha256"] == __import__("hashlib").sha256(json.dumps({k: v for k, v in payload.items() if k != "report_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    # A concurrent edit is an explicit conflict and cannot be presented as a
    # successful rollback/review.
    (tmp_path / "src" / "main.py").write_text("externally changed\n", encoding="utf-8")
    conflict = ReviewBuilder(WorkspaceGuard(tmp_path)).build()
    assert conflict.exit_code == 3
    assert conflict.rollback["available"] is False
    assert conflict.conflicts


def test_review_artifact_is_bound_to_workspace_and_current_file_digests(tmp_path: Path):
    _transaction(tmp_path, content=b"print('ok')\n")
    guard = WorkspaceGuard(tmp_path)
    report = ReviewBuilder(guard).build()
    destination = export_review(report, "review.json", guard)
    imported = import_review(destination, guard)
    assert imported.report_id == report.report_id

    # Tampering with either the artifact envelope or a referenced file is
    # rejected before the report can be consumed.
    raw = json.loads(destination.read_text(encoding="utf-8"))
    raw["created_at"] = "2020-01-01T00:00:00+00:00"
    destination.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReviewArtifactError, match="digest mismatch"):
        import_review(destination, guard)

    destination.unlink()
    destination = export_review(report, "review.json", guard)
    (tmp_path / "src" / "main.py").write_text("stale\n", encoding="utf-8")
    with pytest.raises(ReviewArtifactError, match="stale"):
        import_review(destination, guard)

    with pytest.raises(ReviewArtifactError, match="outside workspace"):
        other = tmp_path.parent / f"{tmp_path.name}-other"
        other.mkdir()
        import_review(destination, WorkspaceGuard(other))


def test_review_rejects_unsafe_limits_and_oversized_or_nonfinite_artifact(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    with pytest.raises(ReviewError):
        ReviewBuilder(guard, max_files=0)
    with pytest.raises(ReviewError):
        run_security_checks(guard, max_commands=float("nan"))  # type: ignore[arg-type]
    report = ReviewBuilder(guard).build()
    destination = export_review(report, "review.json", guard)
    raw = json.loads(destination.read_text(encoding="utf-8"))
    raw["report"]["checks"] = [{"check_id": "secrets", "status": "pass", "source": "x", "severity": "info", "message": "ok", "finding_ids": [], "evidence_refs": [], "scanned": 0, "omitted": 0, "duration_ms": 0, "budget": {}}]
    raw["report"]["report_sha256"] = "0" * 64
    destination.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReviewArtifactError):
        import_review(destination, guard)
