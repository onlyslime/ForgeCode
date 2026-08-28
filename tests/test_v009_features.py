import asyncio
import json
from pathlib import Path

from forgecode.agent import AgentConfig, AgentLoop, ContextBuilder
from forgecode.application.commands import main
from forgecode.context import ContextIndex
from forgecode.evaluation import evaluate_events
from forgecode.models import Message, ModelResponse, ToolCall
from forgecode.security import WorkspaceGuard
from forgecode.storage import SessionStore
from forgecode.tools import AllowAllApproval, ToolContext, build_default_registry


class _LongProvider:
    def __init__(self):
        self.requests = []
        self.calls = 0

    async def complete(self, messages, tools):
        self.requests.append(list(messages))
        self.calls += 1
        if self.calls < 5:
            return ModelResponse(Message("assistant", "x" * 900, tool_calls=(ToolCall(f"t{self.calls}", "list_files", {"pattern": "*"}),)))
        return ModelResponse(Message("assistant", "done"))


def test_auto_compaction_preserves_goal_pairing_and_is_bounded(tmp_path: Path):
    provider = _LongProvider()
    session = SessionStore(tmp_path / "run.jsonl")
    guard = WorkspaceGuard(tmp_path)
    loop = AgentLoop(provider, build_default_registry(guard), ToolContext(guard, AllowAllApproval()), session=session,
                     config=AgentConfig(max_steps=6, max_repeated_calls=10, compact_threshold_chars=700, max_auto_compactions=2),
                     context_builder=ContextBuilder(max_chars=1_200, max_message_chars=900))
    result = asyncio.run(loop.run("preserve this long running task"))
    assert result.succeeded
    events = list(session.read())
    compacted = [event for event in events if event.kind == "context_compacted"]
    assert compacted and compacted[0].payload["reason"] == "automatic"
    assert compacted[0].payload["context_fingerprint"]
    assert any(message.role == "system" and "AUTOMATIC CONTEXT SUMMARY" in message.content for request in provider.requests for message in request)
    assert all(sum(len(message.content) for message in request) <= 1_500 for request in provider.requests)


def test_trajectory_evaluator_rejects_model_claim_without_verification(tmp_path: Path):
    store = SessionStore(tmp_path / "run.jsonl")
    store.append("run_created", {"run_id": store.run_id})
    store.append("model_message", {"content": "tests pass"})
    store.append("final", {"stopped_reason": "model_finished", "state": "completed"})
    score = evaluate_events(tuple(store.read()))
    assert score.status == "failed" and not score.verification_passed and score.score < 1.0


def test_path_completion_is_advisory_and_stable(tmp_path: Path):
    (tmp_path / "alpha.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=hidden\n", encoding="utf-8")
    index = ContextIndex(WorkspaceGuard(tmp_path))
    index.build()
    suggestions = index.complete("a")
    assert suggestions and suggestions[0]["path"] == "alpha.py"
    assert suggestions[0]["excluded"] is False
    for unsafe in ("../", "/", "C:/outside"):
        try:
            index.complete(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe completion prefix accepted: {unsafe}")


def test_cli_eval_and_config_profiles_machine_contract(capsys, tmp_path: Path):
    (tmp_path / ".forgecode").mkdir()
    (tmp_path / ".forgecode" / "config.toml").write_text('[profiles.local]\nmodel="demo"\nbase_url="http://localhost:8000/v1"\n', encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "config", "profiles", "--jsonl"]) == 0
    profile = json.loads(capsys.readouterr().out)
    assert profile["ok"] and profile["data"]["profiles"][1]["name"] == "local"
    store = SessionStore(tmp_path / ".forgecode" / "sessions" / "run.jsonl")
    store.append("final", {"stopped_reason": "model_finished", "state": "completed"})
    assert main(["--workspace", str(tmp_path), "eval", "run", "--jsonl"]) == 1


def test_session_tree_clone_and_import_are_non_replaying(capsys, tmp_path: Path):
    session_dir = tmp_path / ".forgecode" / "sessions"
    session_dir.mkdir(parents=True)
    parent = SessionStore(session_dir / "parent.jsonl")
    parent.append("run_created", {"run_id": parent.run_id}, mode="act")
    parent.append("model_message", {"content": "done"}, mode="act")
    parent.append("final", {"stopped_reason": "model_finished", "state": "completed"}, mode="act")
    assert main(["--workspace", str(tmp_path), "session", "clone", "parent", "--jsonl"]) == 0
    cloned = json.loads(capsys.readouterr().out)
    assert cloned["data"]["replay"] is False
    assert main(["--workspace", str(tmp_path), "session", "tree", "--jsonl"]) == 0
    tree = json.loads(capsys.readouterr().out)
    assert tree["ok"] and any(node["run_id"] == cloned["data"]["run_id"] for node in tree["data"]["nodes"])
    cloned_node = next(node for node in tree["data"]["nodes"] if node["run_id"] == cloned["data"]["run_id"])
    assert cloned_node["parent_run_id"] == parent.run_id
    assert any(edge["child"] == cloned["data"]["run_id"] and edge["parent"] == parent.run_id for edge in tree["data"]["edges"])
    assert main(["--workspace", str(tmp_path), "session", "import", "parent.jsonl", "--jsonl"]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["data"]["replay"] is False
