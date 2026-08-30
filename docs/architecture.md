# ForgeCode architecture

ForgeCode keeps the agent boundary in this repository rather than delegating
it to an agent framework.

```text
CLI / JSONL / SDK
    -> application services and typed configuration
    -> AgentLoop + provider adapter
    -> ContextBuilder and bounded history
    -> ToolRegistry (schemas, validation, mode policy)
    -> WorkspaceGuard -> files, patches, commands and tests
    -> SessionStore -> events, checkpoints, transactions and review evidence
```

Provider adapters normalize OpenAI-compatible, Anthropic, Google, Ollama, and
offline-demo responses into a provider-neutral message and tool-call protocol.
The loop preserves call IDs, errors, approvals, cancellation, exit codes, and
verification results in session events.

Every path is checked by `WorkspaceGuard`. Plan is read-only; Act permits
approved side effects; Bypass requires an explicit trusted-workspace choice.
Writes are atomic, patches are previewed and reversible, and command execution
is bounded by policy, timeout, output, and process limits. This is an
application safety boundary, not an operating-system sandbox.
