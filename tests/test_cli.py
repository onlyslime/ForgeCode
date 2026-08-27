from forgecode.cli import main


def test_doctor_command(capsys, tmp_path):
    assert main(["--workspace", str(tmp_path), "doctor"]) == 0
    output = capsys.readouterr().out
    assert "status: ready" in output
    assert "read_file" in output
