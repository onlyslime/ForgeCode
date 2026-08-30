from forgecode.models import AnthropicProvider, GoogleProvider, OllamaProvider, create_provider
from forgecode.models.factory import _ProtocolTransport
from forgecode.models.openai_compatible import UrllibTransport


def test_provider_factory_selects_named_adapters():
    for name, cls in (("anthropic", AnthropicProvider), ("google", GoogleProvider), ("ollama", OllamaProvider)):
        provider = create_provider(provider=name, api_key="key", base_url="https://example.test/v1", model="m")
        assert isinstance(provider, cls)
        assert provider.provider_name == name


def test_factory_adapters_wrap_default_transport_for_wire_translation():
    for name in ("anthropic", "google", "ollama"):
        provider = create_provider(provider=name, api_key="key", base_url="https://example.test/v1", model="m")
        assert isinstance(provider.transport, _ProtocolTransport)
        assert isinstance(provider.transport.delegate, UrllibTransport)
