"""Small, auditable workspace trust store.

Trust is scoped to the canonical workspace path and can be revoked at any
time.  The record contains no credentials or workspace contents.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .workspace import WorkspaceViolation, assert_no_path_alias


class TrustError(ValueError):
    pass


class TrustStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.path = self.workspace / ".forgecode" / "trust.json"
        self._lock = threading.RLock()

    def _check(self) -> None:
        if not self.workspace.is_dir():
            raise TrustError("workspace is not a directory")
        try:
            # Validate the parent even when trust.json does not exist yet;
            # otherwise a pre-created .forgecode link could redirect writes.
            assert_no_path_alias(self.path.parent, message="trust directory must be workspace-local")
        except WorkspaceViolation as exc:
            raise TrustError(str(exc)) from exc
        if os.path.lexists(self.path):
            try:
                assert_no_path_alias(self.path, message="trust record must be a workspace-local regular file")
            except WorkspaceViolation as exc:
                raise TrustError(str(exc)) from exc

    def status(self) -> dict[str, Any]:
        self._check()
        if not self.path.exists():
            return {"trusted": False, "workspace": "."}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TrustError("trust record is invalid") from exc
        if not isinstance(data, dict) or data.get("workspace") != str(self.workspace) or data.get("trusted") is not True:
            return {"trusted": False, "workspace": "."}
        return {"trusted": True, "workspace": ".", "granted_at": data.get("granted_at"), "version": data.get("version", 1)}

    def grant(self) -> dict[str, Any]:
        with self._lock:
            self._check()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "workspace": str(self.workspace), "trusted": True, "granted_at": int(time.time())}
            descriptor, name = tempfile.mkstemp(prefix="trust-", suffix=".tmp", dir=self.path.parent)
            tmp = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    descriptor = -1
                    stream.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self.path)
            except OSError as exc:
                raise TrustError("could not grant workspace trust") from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                tmp.unlink(missing_ok=True)
            return self.status()

    def revoke(self) -> dict[str, Any]:
        with self._lock:
            self._check()
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                raise TrustError("could not revoke workspace trust") from exc
            return self.status()


__all__ = ["TrustError", "TrustStore"]
