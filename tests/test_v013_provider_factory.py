from forgecode.models import AnthropicProvider, GoogleProvider, OllamaProvider, create_provider


def test_provider_factory_selects_named_adapters():
    for name, cls in (("anthropic", AnthropicProvider), ("google", GoogleProvider), ("ollama", OllamaProvider)):
        provider = create_provider(provider=name, api_key="key", base_url="https://example.test/v1", model="m")
        assert isinstance(provider, cls)
        assert provider.provider_name == name
