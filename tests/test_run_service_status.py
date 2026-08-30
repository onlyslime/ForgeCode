from pathlib import Path

from forgecode.application.run_service import RunService
from forgecode.models import DemoProvider
from forgecode.security import WorkspaceGuard
from forgecode.storage import SessionStore
from forgecode.tools import build_default_registry


def test_run_service_status_snapshot_reports_idle_service(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    service = RunService(
        DemoProvider("calculator"),
        build_default_registry(guard),
        guard,
        SessionStore(tmp_path / ".forgecode" / "session.jsonl"),
    )
    status = service.status_snapshot()
    assert status["active"] is False
    assert status["service_starting"] is False
    assert status["pending_pause"] is False
    assert status["pending_cancel"] is False
