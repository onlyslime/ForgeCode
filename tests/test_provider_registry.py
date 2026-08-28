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
    assert health["data"]["probe"]["reason"] == "offline by default"

    assert main(["--workspace", str(tmp_path), "provider", "health", "--probe", "--json"]) == 0
    probed = json.loads(capsys.readouterr().out)
    assert probed["data"]["probe"]["requested"] is True
    assert probed["data"]["probe"]["performed"] is False


def test_rpc_login_forwards_bounded_provider_params(tmp_path: Path):
    records = list(serve_lines([json.dumps({"id": "login-1", "method": "login", "params": {"provider": "ollama"}, "argv": []})]))
    assert len(records) == 1
    payload = json.loads(records[0])
    assert payload["id"] == "login-1"
    assert payload["method"] == "login"


def test_rpc_login_forwards_profile_selector(tmp_path: Path):
    records = list(serve_lines([json.dumps({"id": "login-profile", "method": "login", "params": {"profile": "local"}, "argv": []})]))
    payload = json.loads(records[-1])
    assert payload["id"] == "login-profile"
    # The profile is validated by the CLI; an absent profile yields a stable
    # structured error rather than being silently ignored.
    assert payload["error"]["code"] == "config_invalid"


def test_login_profile_reports_selected_credential_reference(tmp_path: Path, capsys):
    (tmp_path / ".forgecode").mkdir()
    (tmp_path / ".forgecode" / "config.toml").write_text(
        'profile = "local"\n[profiles.local]\nprovider = "ollama"\nmodel = "llama3"\n', encoding="utf-8"
    )
    assert main(["--workspace", str(tmp_path), "login", "--profile", "local", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["profile"] == "local"
    assert payload["data"]["provider"] == "ollama"
    assert payload["data"]["model"] == "llama3"
    assert payload["data"]["provider"] == "ollama"
    assert payload["data"]["configured"] is True
