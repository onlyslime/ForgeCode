from __future__ import annotations

import json
from pathlib import Path

from forgecode.cli import main


def test_rules_explain_reports_precedence(capsys, tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("root rules", encoding="utf-8")
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("nested rules", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "rules", "explain", "src", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["effective_sources"]
    assert "precedence" in payload["data"]
