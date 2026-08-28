from __future__ import annotations

import json
from pathlib import Path

from forgecode.cli import main
from forgecode.config import provider_metadata
from forgecode.rpc import serve_lines


def test_provider_list_uses_registry_metadata(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "provider", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["providers"] == list(provider_metadata())


def test_login_rejects_unknown_provider(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "login", "--provider", "unknown", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unsupported_provider"


def test_ollama_login_and_health_are_local_without_key(capsys, tmp_path: Path):
    assert main(["--workspace", str(tmp_path), "login", "--provider", "ollama", "--json"]) == 0
    login = json.loads(capsys.readouterr().out)
    assert login["data"]["configured"] is True
    assert login["data"]["credential"] == "optional"

    assert main(["--workspace", str(tmp_path), "provider", "health", "--json"]) == 0
    health = json.loads(capsys.readouterr().out)
    assert health["data"]["configured"] is False  # no model selected


def test_rpc_login_forwards_bounded_provider_params(tmp_path: Path):
    records = list(serve_lines([json.dumps({"id": "login-1", "method": "login", "params": {"provider": "ollama"}, "argv": []})]))
    assert len(records) == 1
    payload = json.loads(records[0])
    assert payload["id"] == "login-1"
    assert payload["method"] == "login"
    assert payload["data"]["provider"] == "ollama"
    assert payload["data"]["configured"] is True
