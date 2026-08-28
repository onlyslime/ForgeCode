import json

from forgecode.rpc import serve_lines
from forgecode.embed import invoke


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


def test_rpc_preserves_request_id_and_embedded_api(tmp_path):
    request = json.dumps({"id": "req-7", "argv": ["--workspace", str(tmp_path), "trust", "status", "--jsonl"]})
    result = json.loads(next(serve_lines([request])))
    assert result["id"] == "req-7"
    embedded = invoke(["--workspace", str(tmp_path), "trust", "status", "--jsonl"], request_id=9)
    assert embedded[0]["id"] == 9


def test_rpc_method_dispatch_and_rejection(tmp_path):
    request = json.dumps({"id": "m1", "method": "provider.list"})
    result = json.loads(next(serve_lines([request])))
    assert result["method"] == "provider.list"
    assert result["data"]["providers"]
    bad = json.loads(next(serve_lines([json.dumps({"method": "unknown"})])))
    assert bad["error"]["code"] == "invalid_request"


def test_rpc_config_and_doctor_methods(tmp_path):
    for name in ("config.show", "doctor"):
        payload = json.loads(next(serve_lines([json.dumps({"id": name, "method": name})])))
        assert payload["id"] == name
        assert payload["method"] == name
        assert payload["ok"] is True
