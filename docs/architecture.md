# ForgeCode architecture

ForgeCode is a local-first coding-agent framework. The repository owns the
protocol, conversation state, model/tool loop, execution policy, persistence,
and command-line interfaces; no external agent framework is required.

## Runtime pipeline

```text
CLI / JSONL RPC / Python embed / Node client
    -> typed configuration and application services
    -> rules, references, plan, and bounded context
    -> AgentLoop
       -> provider adapter (OpenAI-compatible, Anthropic, Google, Ollama,
          or deterministic offline demo)
       -> provider-neutral messages and validated tool calls
    -> ToolRegistry (schema, argument, mode, and risk checks)
       -> WorkspaceGuard -> files, search, patches, commands, tests
    -> SessionStore -> events, checkpoints, transactions, review evidence
```

## Main components

### CLI and application layer

`src/forgecode/cli.py` exposes interactive `fcc`/`forgecode` commands and
machine-readable JSON/JSONL modes. Application services share the same typed
configuration and safety contracts, so the REPL, RPC, embed API, and SDKs do
not implement separate execution paths.

### AgentLoop and providers

`src/forgecode/agent/loop.py` drives bounded model turns. It tracks lifecycle
state, step/tool budgets, deadlines, cancellation, pause/resume boundaries,
provider retries, and verification. Provider adapters translate wire formats
to a provider-neutral message/tool-call shape while preserving IDs, finish
reasons, streaming fragments, and error categories.

### Tools and safety boundary

`ToolRegistry` exposes schemas and rejects malformed or unavailable calls.
`WorkspaceGuard` resolves every path, prevents traversal and forbidden
locations, and limits file sizes. Plan is read-only; Act allows approved side
effects; Bypass requires explicit workspace trust. Approval is global or
scoped to `changes`, `execution`, and `evidence`. Commands and tests have
timeouts, output/byte limits, cancellation, and safe process-tree cleanup.
Writes are atomic; patches are previewed, validated, recorded, and reversible.

### Context, rules, and extensions

Rules from scoped `AGENTS.md` files, explicit references, repository maps, and
the incremental context index are normalized into bounded prompt context.
Skills and lifecycle hooks are validated, quota-limited, and cannot bypass
mode policy or `WorkspaceGuard`.

### Persistence and evidence

Sessions store ordered JSONL events with correlation IDs and model/tool
metadata. Checkpoints support recovery after pause, cancellation, or process
failure. Transactions retain before/after hashes and verification evidence for
hash-checked undo. `review` combines session, plan, context, test, hook, and
transaction evidence into a bounded report; export/verify detects workspace or
artifact tampering.

## Extension points

Providers implement the model-provider protocol; tools register schemas and a
handler; test profiles define bounded argv commands in `.forgecode/tests.toml`;
the Python embed and Node JSONL client consume the same RPC contract. Keep
extensions deterministic, bounded, and explicit about side effects.

## Design boundary

The model proposes actions, but local code executes them and records outcomes.
`WorkspaceGuard` is an application policy boundary, not an operating-system
sandbox. Run ForgeCode with the least filesystem and credential access needed
for the workspace.
