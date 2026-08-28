from forgecode.application.commands import main


def test_configured_provider_act_requires_trust(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("FORGECODE_MODEL", "m")
    monkeypatch.setenv("FORGECODE_API_KEY", "k")
    code = main(["--workspace", str(tmp_path), "run", "hello", "--mode", "act", "--jsonl"])
    assert code == 2
    assert "trust_required" in capsys.readouterr().out
