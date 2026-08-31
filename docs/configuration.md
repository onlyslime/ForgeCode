# Configuration and providers

ForgeCode reads typed settings from environment variables and ignored local
configuration under `.forgecode/`. `forgecode config validate` is the safe way
to check the effective result; `config show` and `config profiles` redact
credential values.

## Credential boundary

Use `forgecode login` or environment variables for API keys. Store the name of
an environment variable in a profile, never its value. Keys are not written to
sessions, telemetry, RPC envelopes, review artifacts, or provider errors.

## Profiles

Profiles identify a provider, model, endpoint, streaming preference, and key
reference. `config profiles` reports provider/model/capabilities and whether a
key is present. `/model list`, `/model show NAME`, and `/model select NAME` in
chat switch profiles and record the change in session evidence.

```toml
[profiles.local]
provider = "openai-compatible"
model = "my-model"
base_url = "https://example.invalid/v1"
api_key_env = "FORGECODE_API_KEY"
streaming = true
```

Supported adapters are OpenAI-compatible, Anthropic, Google, Ollama, and the
deterministic offline provider. Capabilities are negotiated; unsupported
streaming or tool calls fail clearly or use the documented bounded fallback.

## Policy settings

Configuration can narrow tools, set approval rules, choose telemetry (`off`,
`local`, or `on` as an external-capability flag), and enable offline mode.
Runtime flags may narrow access further but cannot widen a file or command
boundary. Malformed values fail closed; run `config policy` to explain the
effective decision for each tool.

