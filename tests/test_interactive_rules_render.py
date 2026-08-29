from forgecode.application.interactive_service import _human_result


def test_human_rules_result_renders_sources_and_diagnostics():
    rendered = _human_result(
        {
            "sources": [{"path": "AGENTS.md", "scope": ".", "truncated": False}],
            "diagnostics": [],
            "fingerprint": "abcdef0123456789deadbeef",
            "chars": 42,
        }
    )
    assert rendered is not None
    assert "Rules" in rendered
    assert "status: active" in rendered
    assert "AGENTS.md" in rendered
    assert "Diagnostics: none" in rendered


def test_human_rules_result_shows_errors():
    rendered = _human_result(
        {
            "sources": [],
            "diagnostics": [{"severity": "error", "message": "outside workspace", "path": "x"}],
            "fingerprint": "",
            "chars": 0,
        }
    )
    assert "status: error" in rendered
    assert "outside workspace (x)" in rendered
