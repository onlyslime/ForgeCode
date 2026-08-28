from pathlib import Path

from forgecode.security.trust import TrustStore


def test_workspace_trust_grant_and_revoke(tmp_path: Path):
    store = TrustStore(tmp_path)
    assert store.status()["trusted"] is False
    granted = store.grant()
    assert granted["trusted"] is True
    assert (tmp_path / ".forgecode" / "trust.json").exists()
    assert store.revoke()["trusted"] is False
