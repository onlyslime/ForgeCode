from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from forgecode.security.trust import TrustStore


def test_workspace_trust_grant_and_revoke(tmp_path: Path):
    store = TrustStore(tmp_path)
    assert store.status()["trusted"] is False
    granted = store.grant()
    assert granted["trusted"] is True
    assert (tmp_path / ".forgecode" / "trust.json").exists()
    assert store.revoke()["trusted"] is False


def test_concurrent_grants_use_independent_atomic_temporary_files(tmp_path: Path):
    store = TrustStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: store.grant(), range(8)))
    assert all(result["trusted"] for result in results)
    assert store.status()["trusted"] is True
