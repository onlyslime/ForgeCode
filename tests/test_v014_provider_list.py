import json

from forgecode.application.commands import main


def test_provider_list_machine_contract(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "provider", "list", "--jsonl"]) == 0
    payload = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in payload["data"]["providers"]}
    assert names == {"openai-compatible", "anthropic", "google", "ollama"}
    assert all(item["streaming"] for item in payload["data"]["providers"])
