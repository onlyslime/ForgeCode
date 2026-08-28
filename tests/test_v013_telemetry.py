from forgecode.telemetry import Telemetry
from forgecode.application.commands import main
import json


def test_offline_telemetry_does_not_write(tmp_path):
    telemetry = Telemetry(tmp_path, mode="local", offline=True)
    assert telemetry.record("run", prompt="secret") is False
    assert not (tmp_path / ".forgecode" / "telemetry.jsonl").exists()


def test_local_telemetry_is_bounded(tmp_path):
    telemetry = Telemetry(tmp_path, mode="local")
    assert telemetry.record("run", prompt="secret", nested={"x": 1}) is True
    text = (tmp_path / ".forgecode" / "telemetry.jsonl").read_text()
    assert "secret" in text and "nested" not in text


def test_telemetry_cli_status_and_export(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "telemetry", "status", "--jsonl"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["data"]["mode"] == "off"
    assert main(["--workspace", str(tmp_path), "telemetry", "export", "--jsonl"]) == 0
    export = json.loads(capsys.readouterr().out)
    assert export["data"]["records"] == []
