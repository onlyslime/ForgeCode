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


def test_human_files_and_skills_results_render():
    files = _human_result({"prefix": "src", "results": ["src/main.py"], "advisory": True})
    assert "Files" in files and "src/main.py" in files and "matches: 1" in files
    skills = _human_result({"skills": [{"manifest": {"id": "demo", "name": "Demo", "entry_type": "markdown", "description": "A demo"}}], "errors": []})
    assert "Skills" in skills and "demo — Demo" in skills and "A demo" in skills
