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


def test_human_tree_review_and_compact_results_render():
    tree = _human_result({"nodes": [{"run_id": "r1", "state": "completed", "events": 3}], "roots": ["r1"], "edges": []})
    assert "Session tree" in tree and "r1" in tree
    review = _human_result({"transaction_id": "tx1", "state": "committed", "rollback_available": True})
    assert "Review" in review and "rollback: available" in review
    compact = _human_result({"before_chars": 100, "after_chars": 40, "omitted_messages": 2, "summary": "kept recent context"})
    assert "Context compacted" in compact and "kept recent context" in compact
