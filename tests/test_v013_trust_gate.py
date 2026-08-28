from forgecode.application.commands import main


def test_require_trust_gate_for_act_mode(capsys, tmp_path):
    code = main(["--workspace", str(tmp_path), "run", "hello", "--mode", "act", "--require-trust", "--jsonl"])
    assert code == 2
    assert "trust_required" in capsys.readouterr().out
