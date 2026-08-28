"""Small, auditable workspace trust store.

Trust is scoped to the canonical workspace path and can be revoked at any
time.  The record contains no credentials or workspace contents.
"""
from __future__ import annotations

import json
import os
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

    def _check(self) -> None:
        if not self.workspace.is_dir():
            raise TrustError("workspace is not a directory")
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
        self._check()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "workspace": str(self.workspace), "trusted": True, "granted_at": int(time.time())}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        return self.status()

    def revoke(self) -> dict[str, Any]:
        self._check()
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise TrustError("could not revoke workspace trust") from exc
        return self.status()


__all__ = ["TrustError", "TrustStore"]
