import json

from forgecode.rpc import serve_lines


def test_rpc_preserves_cli_envelope(tmp_path):
    request = json.dumps({"argv": ["--workspace", str(tmp_path), "trust", "status", "--jsonl"]})
    result = json.loads(next(serve_lines([request])))
    assert result["schema_version"] == 1
    assert result["command"] == "trust status"
    assert result["ok"] is True


def test_rpc_forwards_each_cli_jsonl_event(tmp_path):
    request = json.dumps({"argv": ["--workspace", str(tmp_path), "run", "--demo", "--auto-approve", "--jsonl"]})
    results = [json.loads(item) for item in serve_lines([request])]
    assert results
    assert any(item.get("kind") == "result" for item in results)
