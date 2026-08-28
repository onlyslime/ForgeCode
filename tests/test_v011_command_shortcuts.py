"""Focused contracts for Pi-inspired !/!! interactive shortcuts."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from forgecode.application import InteractiveSession, ShortcutParseError, parse_command_shortcut
from forgecode.application import commands as command_module
from forgecode.models import Message, ModelResponse


def test_shortcut_parser_is_prefix_only_and_bounded() -> None:
    assert parse_command_shortcut("! echo hi").kind == "model"
    assert parse_command_shortcut("!! echo hi").kind == "local"
    assert parse_command_shortcut("ordinary ! punctuation") is None
    assert parse_command_shortcut("  ! echo hi") is None
    with pytest.raises(ShortcutParseError) as empty:
        parse_command_shortcut("!!")
    assert empty.value.code == "shortcut_empty"
    with pytest.raises(ShortcutParseError) as ambiguous:
        parse_command_shortcut("!!! echo hi")
    assert ambiguous.value.code == "shortcut_prefix"
    with pytest.raises(ShortcutParseError) as multiline:
        parse_command_shortcut("! echo hi\nthere")
    assert multiline.value.code == "shortcut_multiline"
    with pytest.raises(ShortcutParseError) as oversized:
        parse_command_shortcut("! " + "x" * 4_001)
    assert oversized.value.code == "shortcut_too_long"


def test_interactive_dispatch_reports_malformed_shortcut_without_running_it() -> None:
    calls: list[str] = []
    session = InteractiveSession(lambda message: calls.append(message) or {"message": message})
    result = session.dispatch("!")
    assert result == {"accepted": False, "shortcut": True, "error": "! command must not be empty", "code": "shortcut_empty"}
    assert calls == []


def test_local_shortcut_uses_shell_tool_and_never_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class ExplodingProvider:
        def __init__(self, **_: object) -> None:
            raise AssertionError("!! shortcut must not construct a provider")

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", ExplodingProvider)
    monkeypatch.setattr("sys.stdin", io.StringIO("!! echo token=hidden-value\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    result = next(record for record in records if record.get("kind") == "interactive_result" and record.get("data", {}).get("shortcut") == "!!")
    assert result["ok"] is True
    payload = result["data"]
    assert "hidden-value" not in json.dumps(payload)
    assert payload["metadata"]["stdout"].startswith("token=[REDACTED]")
    session_path = next((tmp_path / ".forgecode" / "sessions").glob("*.jsonl"))
    text = session_path.read_text(encoding="utf-8")
    assert text.count('"kind":"command_shortcut"') == 1
    assert "hidden-value" not in text
    assert '"shortcut":"!!"' in text


def test_model_shortcut_feeds_bounded_result_to_one_provider_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    requests: list[tuple[tuple[Message, ...], tuple[dict, ...]]] = []

    class Provider:
        def __init__(self, **_: object) -> None:
            pass

        async def complete(self, messages, tools, context=None):  # type: ignore[no-untyped-def]
            requests.append((tuple(messages), tuple(tools)))
            return ModelResponse(Message("assistant", "shortcut follow-up complete"))

    monkeypatch.setattr(command_module, "OpenAICompatibleProvider", Provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("! echo shortcut-ok\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    result = next(record for record in records if record.get("kind") == "interactive_result" and record.get("data", {}).get("stopped_reason"))
    assert result["data"]["succeeded"] is True
    assert len(requests) == 1
    assert any("shortcut-ok" in message.content for message in requests[0][0])
    session_path = next((tmp_path / ".forgecode" / "sessions").glob("*.jsonl"))
    text = session_path.read_text(encoding="utf-8")
    assert text.count('"kind":"command_shortcut"') == 1
    assert '"shortcut":"!"' in text


def test_shortcut_marks_its_own_output_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO('!! python -c "print(\'x\' * 5000)"\n'))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    result = next(record for record in records if record.get("payload", {}).get("shortcut") == "!!")
    assert result["payload"]["truncated"] is True
    assert len(result["payload"]["output"]) <= 4_000
    assert len(result["payload"]["metadata"]["stdout"]) <= 4_000
    assert len(result["payload"]["metadata"]["stderr"]) <= 4_000


def test_shortcut_is_denied_in_plan_mode_before_shell_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("!! echo should-not-run\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "plan", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    result = next(record for record in records if record.get("kind") == "error")
    assert result["error"]["code"] == "mode_denied"
    assert not (tmp_path / "should-not-run").exists()
    session_path = next((tmp_path / ".forgecode" / "sessions").glob("*.jsonl"))
    assert '"kind":"command_shortcut"' in session_path.read_text(encoding="utf-8")


def test_failed_shortcut_uses_error_envelope_and_safe_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("!! python -c \"import sys; print('secret=bad'); sys.exit(4)\"\n"))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    result = next(record for record in records if record.get("payload", {}).get("shortcut") == "!!" and record.get("payload", {}).get("ok") is False)
    assert result["ok"] is False
    assert result["error"]["code"] == "command_failed"
    assert "secret=bad" not in json.dumps(records)


def test_shortcut_cancel_terminates_command_without_false_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO('!! python -c "import time; time.sleep(5)"\n/cancel\n'))
    assert command_module.main(["--workspace", str(tmp_path), "chat", "--mode", "act", "--jsonl", "--auto-approve"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    shortcut_records = [record for record in records if record.get("payload", {}).get("shortcut") == "!!"]
    assert shortcut_records
    payload = shortcut_records[-1]["payload"]
    assert payload["ok"] is False
    assert payload["cancelled"] is True
    assert shortcut_records[-1]["ok"] is False
