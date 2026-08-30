import json
from pathlib import Path

import pytest

from forgecode.config import ConfigError, ConfigLoader, ModelProfile
from forgecode.models import Message, ProviderError, assemble_chat_stream, parse_chat_completion
from forgecode.models.openai_compatible import _sse_json_events


def test_config_precedence_cli_over_file_over_environment(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / ".forgecode"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('model = "file-model"\nmax_steps = 20\n[tool_policy]\ndeny = ["run_command"]\n', encoding="utf-8")
    monkeypatch.setenv("FORGECODE_MODEL", "env-model")
    config = ConfigLoader(tmp_path).load({"model": "cli-model"})
    assert config.model == "cli-model" and config.max_steps == 20
    assert not config.tool_policy.permits("run_command", available={"run_command"})
    assert config.to_dict()["api_key"] == "<environment-only>"


def test_config_named_profile_validation_and_secret_field_rejection(tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    (directory / "config.toml").write_text('[profiles.local]\nmodel="m"\nbase_url="http://localhost:1234/v1"\nstreaming="off"\n', encoding="utf-8")
    config = ConfigLoader(tmp_path).load(profile="local")
    assert config.profile == "local" and config.model == "m"
    (directory / "config.toml").write_text('api_key="plaintext-secret"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown config fields"):
        ConfigLoader(tmp_path).load()


def test_explicit_default_profile_does_not_get_overridden_by_file_profile(tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    (directory / "config.toml").write_text('profile = "local"\n[profiles.local]\nmodel="local-model"\n', encoding="utf-8")
    config = ConfigLoader(tmp_path).load(profile="default")
    assert config.profile == "default" and config.model is None


def test_named_profile_overrides_root_toml_but_cli_remains_highest(tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    (directory / "config.toml").write_text('model="root-model"\n[tool_policy]\ndeny=["run_command"]\n[profiles.local]\nmodel="profile-model"\n[profiles.local.tool_policy]\ndeny=["apply_patch"]\n', encoding="utf-8")

    profile = ConfigLoader(tmp_path).load(profile="local")
    overridden = ConfigLoader(tmp_path).load({"model": "cli-model"}, profile="local")

    assert profile.model == "profile-model"
    assert profile.tool_policy.deny == ("apply_patch",)
    assert overridden.model == "cli-model"


def test_config_rejects_unsupported_provider(tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    (directory / "config.toml").write_text('provider = "unsupported-agent"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported"):
        ConfigLoader(tmp_path).load()


def test_config_rejects_bool_as_int_and_malformed_toml(tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    path = directory / "config.toml"
    path.write_text("max_steps = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_steps"):
        ConfigLoader(tmp_path).load()
    path.write_text("[[[broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid config"):
        ConfigLoader(tmp_path).load()


def test_model_profile_validation_is_independent_and_does_not_reference_missing_fields():
    ModelProfile(name="local", base_url="http://localhost:8000/v1", model="demo").validate()
    with pytest.raises(ConfigError, match="profile.base_url"):
        ModelProfile(name="local", base_url="not-a-url").validate()


@pytest.mark.parametrize(
    "url, message",
    [
        ("https://user:password@example.test/v1", "credentials"),
        ("https://example.test/v1?token=fake", "query"),
        ("https://example.test/v1#fragment", "query or fragment"),
        ("https://[broken/v1", "valid"),
    ],
)
def test_config_rejects_credential_query_fragment_and_malformed_urls(tmp_path: Path, url: str, message: str):
    config_dir = tmp_path / ".forgecode"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(f'base_url = "{url}"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        ConfigLoader(tmp_path).load()


def test_config_rejects_broken_or_symlink_config_entry(tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    config = directory / "config.toml"
    try:
        config.symlink_to(tmp_path / "missing-config.toml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ConfigError, match="symlink"):
        ConfigLoader(tmp_path).load()


def test_config_rejects_nested_unknown_fields_without_echoing_secret(tmp_path: Path):
    directory = tmp_path / ".forgecode"
    directory.mkdir()
    (directory / "config.toml").write_text('[profiles.local]\nmodel="m"\nunknown_field=true\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown fields in profile local"):
        ConfigLoader(tmp_path).load(profile="local")
    (directory / "config.toml").write_text('[profiles.local]\napi_key="top-secret"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="plaintext secret") as error:
        ConfigLoader(tmp_path).load(profile="local")
    assert "top-secret" not in str(error.value)


def test_config_rejects_file_changed_during_parse(monkeypatch, tmp_path: Path):
    import tomllib

    directory = tmp_path / ".forgecode"
    directory.mkdir()
    path = directory / "config.toml"
    path.write_text('model = "one"\n', encoding="utf-8")
    original = tomllib.load

    def changing_load(stream):
        parsed = original(stream)
        path.write_text('model = "changed-model"\n', encoding="utf-8")
        return parsed

    monkeypatch.setattr(tomllib, "load", changing_load)
    with pytest.raises(ConfigError, match="changed while it was read"):
        ConfigLoader(tmp_path).load()


def test_config_toml_has_bounded_size_and_recursion_errors_are_structured(monkeypatch, tmp_path: Path):
    import tomllib

    directory = tmp_path / ".forgecode"
    directory.mkdir()
    path = directory / "config.toml"
    path.write_text("#" + ("x" * 1_000_000), encoding="utf-8")
    with pytest.raises(ConfigError, match="safety limit"):
        ConfigLoader(tmp_path).load()

    path.write_text('model = "safe"\n', encoding="utf-8")
    monkeypatch.setattr(tomllib, "load", lambda _stream: (_ for _ in ()).throw(RecursionError("deep TOML")))
    with pytest.raises(ConfigError, match="nesting"):
        ConfigLoader(tmp_path).load()


def test_sse_assembler_completes_fragmented_tool_arguments_only_at_done():
    chunks = [
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"c1","function":{"name":"read_file","arguments":"{\\"pa"}}]}}]}\r\n',
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\":\\"a.py\\"}"}}]},"finish_reason":"tool_calls"}]}\n',
        b'data: [DONE]\n',
    ]
    events, done = _sse_json_events(chunks)
    response = assemble_chat_stream(events)
    assert done and response.message.tool_calls[0].arguments == {"path": "a.py"}


def test_sse_assembler_emits_text_deltas_without_changing_response():
    chunks = [
        b'data: {"choices":[{"index":0,"delta":{"content":"hello "}}]}\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"world"},"finish_reason":"stop"}]}\n',
        b'data: [DONE]\n',
    ]
    events, _ = _sse_json_events(chunks)
    deltas: list[str] = []
    response = assemble_chat_stream(events, on_text_delta=deltas.append)
    assert response.message.content == "hello world"
    assert deltas == ["hello ", "world"]


def test_broken_sse_and_incomplete_json_return_no_tool_call():
    with pytest.raises(ProviderError, match=r"before \[DONE\]"):
        _sse_json_events([b'data: {"choices":[]}\n'])
    events, _ = _sse_json_events([b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"x","function":{"name":"write_file","arguments":"{\\"path\\":"}}]}}]}\n', b'data: [DONE]\n'])
    with pytest.raises(ProviderError, match="incomplete JSON"):
        assemble_chat_stream(events)


def test_sse_rejects_duplicate_tool_ids():
    events = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "same", "function": {"name": "a", "arguments": "{}"}}, {"index": 1, "id": "same", "function": {"name": "b", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
    ]
    with pytest.raises(ProviderError, match="repeats"):
        assemble_chat_stream(events)


def test_sse_rejects_duplicate_done_and_data_after_done():
    with pytest.raises(ProviderError, match=r"repeated \[DONE\]"):
        _sse_json_events([b"data: [DONE]\n", b"data: [DONE]\n"])
    with pytest.raises(ProviderError, match=r"after \[DONE\]"):
        _sse_json_events([b"data: [DONE]\n", b'data: {"choices": []}\n'])


def test_provider_rejects_nonfinite_tool_arguments_and_finish_reason_mismatch():
    with pytest.raises(ProviderError, match="invalid JSON arguments"):
        parse_chat_completion({"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [{"id": "x", "function": {"name": "write_file", "arguments": '{"value":NaN}'}}]}}]})
    with pytest.raises(ProviderError, match="finish_reason=tool_calls"):
        parse_chat_completion({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "tool_calls": [{"id": "x", "function": {"name": "read_file", "arguments": "{}"}}]}}]})
    with pytest.raises(ProviderError, match="has no tool calls"):
        parse_chat_completion({"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": ""}}]})


def test_stream_rejects_nonfinite_tool_arguments():
    events = [{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "x", "function": {"name": "write_file", "arguments": '{"value":NaN}'}}]}, "finish_reason": "tool_calls"}]}]
    with pytest.raises(ProviderError, match="incomplete JSON"):
        assemble_chat_stream(events)


def test_stream_rejects_nonfinite_usage():
    events = [{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": float("nan")}}]
    with pytest.raises(ProviderError, match="non-finite"):
        assemble_chat_stream(events)


def test_stream_transport_not_implemented_falls_back_only_when_optional():
    class OptionalStreamTransport:
        def __init__(self):
            self.json_calls = 0

        def post_stream(self, *_args):
            raise NotImplementedError("SSE unavailable")

        def post_json(self, *_args):
            self.json_calls += 1
            return 200, b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"fallback"}}]}'

    import asyncio
    from forgecode.models import Message, OpenAICompatibleProvider

    transport = OptionalStreamTransport()
    provider = OpenAICompatibleProvider(api_key="key", base_url="https://example.test/v1", model="m", transport=transport, streaming=True, stream_required=False)
    response = asyncio.run(provider.complete([Message("user", "hi")], []))
    assert response.message.content == "fallback" and transport.json_calls == 1
    required = OpenAICompatibleProvider(api_key="key", base_url="https://example.test/v1", model="m", transport=transport, streaming=True, stream_required=True)
    with pytest.raises(ProviderError, match="required"):
        asyncio.run(required.complete([Message("user", "hi")], []))


def test_stream_http_capability_error_falls_back_to_json_when_optional():
    import asyncio
    from forgecode.models import OpenAICompatibleProvider
    class NoSseTransport:
        def post_stream(self, *_args):
            return 405, iter(())
        def post_json(self, *_args):
            return 200, b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"json fallback"}}]}'

    provider = OpenAICompatibleProvider(api_key="key", base_url="https://example.test/v1", model="m", transport=NoSseTransport(), streaming=True, stream_required=False)
    response = asyncio.run(provider.complete([Message("user", "hi")], []))
    assert response.message.content == "json fallback"


def test_stream_accepts_usage_only_terminal_frame_after_finish():
    events = [
        {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}},
    ]
    response = assemble_chat_stream(events)
    assert response.message.content == "ok"
    assert response.usage == {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}


@pytest.mark.parametrize("finish_reason", ["bogus", "", 1])
def test_provider_rejects_unknown_or_invalid_finish_reason(finish_reason):
    with pytest.raises(ProviderError, match="finish_reason"):
        parse_chat_completion({"choices": [{"finish_reason": finish_reason, "message": {"content": "ok"}}]})


@pytest.mark.parametrize("usage", [{"total_tokens": -1}, {"total_tokens": True}, {"total_tokens": "3"}, {"x" * 129: 1}])
def test_provider_rejects_invalid_usage_values(usage):
    with pytest.raises(ProviderError, match="usage"):
        parse_chat_completion({"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": usage})


def test_stream_transient_http_error_retries_before_success():
    class RetryStreamTransport:
        def __init__(self):
            self.calls = 0

        def post_stream(self, *_args):
            self.calls += 1
            if self.calls == 1:
                return 503, iter(())
            return 200, iter([
                b'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n',
                b'data: [DONE]\n',
            ])

        def post_json(self, *_args):
            raise AssertionError("successful stream should not use fallback")

    transport = RetryStreamTransport()
    provider = __import__("forgecode.models", fromlist=["OpenAICompatibleProvider"]).OpenAICompatibleProvider(
        api_key="key", base_url="https://example.test/v1", model="m", transport=transport,
        streaming=True, retry_base_delay=0,
    )
    response = __import__("asyncio").run(provider.complete([Message("user", "hi")], []))
    assert response.message.content == "ok" and transport.calls == 2
    assert provider.retry_events and provider.retry_events[0]["category"] == "stream_http_503"


def test_stream_protocol_failure_retries_before_exposing_tool_calls():
    class InterruptedThenValidTransport:
        def __init__(self):
            self.calls = 0

        def post_stream(self, *_args):
            self.calls += 1
            if self.calls == 1:
                # The first request contains a partial side-effecting tool call
                # and then loses [DONE]. It must never escape the provider.
                return 200, iter([
                    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"w1","function":{"name":"write_file","arguments":"{\\"path\\":"}}]}}]}\n',
                ])
            return 200, iter([
                b'data: {"choices":[{"index":0,"delta":{"content":"recovered"},"finish_reason":"stop"}]}\n',
                b'data: [DONE]\n',
            ])

        def post_json(self, *_args):
            raise AssertionError("protocol retry should stay on the stream path")

    transport = InterruptedThenValidTransport()
    provider = __import__("forgecode.models", fromlist=["OpenAICompatibleProvider"]).OpenAICompatibleProvider(
        api_key="key", base_url="https://example.test/v1", model="m", transport=transport,
        streaming=True, retry_base_delay=0, max_retries=1,
    )
    response = __import__("asyncio").run(provider.complete([Message("user", "hi")], []))

    assert response.message.content == "recovered"
    assert response.message.tool_calls == ()
    assert transport.calls == 2
    assert provider.retry_events[0]["category"] == "stream_incomplete"
    assert [item["outcome"] for item in provider.attempt_events] == ["error", "success"]


def test_provider_honors_bounded_retry_after_header_from_transport():
    class RetryAfterTransport:
        def __init__(self):
            self.calls = 0

        def post_json(self, *_args):
            self.calls += 1
            if self.calls == 1:
                return 429, b'{"error":{"message":"busy"}}', {"Retry-After": "0"}
            return 200, b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"ok"}}]}'

    transport = RetryAfterTransport()
    provider = __import__("forgecode.models", fromlist=["OpenAICompatibleProvider"]).OpenAICompatibleProvider(
        api_key="key", base_url="https://example.test/v1", model="m", transport=transport,
        retry_base_delay=1,
    )
    response = __import__("asyncio").run(provider.complete([Message("user", "hi")], []))
    assert response.message.content == "ok" and transport.calls == 2
    assert provider.retry_events[0]["retry_after"] == "0"
    assert provider.retry_events[0]["delay_seconds"] < 0.1
