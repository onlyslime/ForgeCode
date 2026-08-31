# Provider capability matrix

| Adapter | Credentials | Streaming | Native tools | Offline |
|---|---|---:|---:|---:|
| OpenAI-compatible | profile key environment reference | negotiated | negotiated | no |
| Anthropic | profile key environment reference | negotiated | negotiated | no |
| Google | profile key environment reference | negotiated | negotiated | no |
| Ollama | local endpoint/profile | negotiated | negotiated | usually local |
| Deterministic demo | none | deterministic | deterministic | yes |

Use `provider list` to see adapters and `provider health` to inspect the
selected configuration without making a model request. A missing key,
unsupported capability, HTTP failure, deadline, cancellation, or retry is
reported with a bounded categorized diagnostic and request correlation id.
Provider response bodies are never copied wholesale into audit output.

