from pathlib import Path

import pytest

from forgecode.memory import MemoryError, MemoryStore
from forgecode.agent import AgentLoop
from forgecode.models import Message, ModelResponse
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry
from forgecode.security.workspace import WorkspaceGuard


def test_memory_store_round_trip_and_remove(tmp_path: Path):
    store = MemoryStore(WorkspaceGuard(tmp_path))
    entry = store.add("Use uv for dependency management")
    assert store.read() == (entry,)
    assert entry.id in store.prompt()
    assert store.remove(entry.id) == entry
    assert store.read() == ()


def test_memory_store_bounds_duplicates_and_clear(tmp_path: Path):
    store = MemoryStore(WorkspaceGuard(tmp_path))
    with pytest.raises(MemoryError, match="empty"):
        store.add(" ")
    store.add("Keep changes small")
    with pytest.raises(MemoryError, match="identical"):
        store.add("Keep changes small")
    assert store.clear() == 1


def test_memory_store_rejects_tampered_schema(tmp_path: Path):
    folder = tmp_path / ".forgecode"
    folder.mkdir()
    (folder / "memory.json").write_text('{"schema_version": 9, "entries": []}', encoding="utf-8")
    with pytest.raises(MemoryError, match="unsupported schema"):
        MemoryStore(WorkspaceGuard(tmp_path)).read()


def test_memory_is_injected_as_untrusted_context_and_redacted(tmp_path: Path):
    store = MemoryStore(WorkspaceGuard(tmp_path))
    store.add("remember secret-value only as a test")
    observed = []

    class Provider:
        async def complete(self, messages, _tools):
            observed.append(messages[0].content)
            return ModelResponse(Message("assistant", "done"), finish_reason="stop")

    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(Provider(), build_default_registry(guard), ToolContext(guard, AllowAllApproval(), secrets=("secret-value",), memory_context=store.prompt()))
    import asyncio
    assert asyncio.run(loop.run("check memory")).succeeded
    assert "Workspace memory" in observed[0]
    assert "secret-value" not in observed[0]
