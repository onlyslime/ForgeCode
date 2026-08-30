# Implemented capabilities (v0.8.1)

This is the maintained, user-visible capability inventory for the current
release. Claims are backed by the deterministic regression suite and the
offline walkthrough in [`demo-script.md`](demo-script.md). Source and test
locations are mapped in [`capability-trace.md`](capability-trace.md).

## Model protocol and loop

- Provider-neutral messages and native tool calling.
- OpenAI-compatible, Anthropic, Google, and Ollama adapters, plus an offline
  deterministic provider for demos and CI.
- Streaming text and tool-call fragments with normalized finish reasons.
- Bounded provider retries, deadlines, cancellation, cleanup grace periods,
  and categorized errors.
- AgentLoop lifecycle states, step/tool budgets, turn correlation IDs, and
  verification-aware completion.

## Workspace tools and execution

- Workspace-aware file listing, UTF-8 reads, text/regex search, repository map,
  symbols, definitions, references, and metadata.
- Atomic file creation/writes, unified multi-file patches, previews, and
  transaction records with before/after hashes.
- Bounded shell commands and named test profiles with argv validation,
  timeout, output limits, exit-code checks, cancellation, and process-tree
  termination.
- Hard blocks for traversal, forbidden paths, unsafe commands, and secret
  exposure; failures preserve stdout, stderr, exit codes, and structured
  error metadata.

## Modes, approvals, and context

- Plan (read-only), Act (approved side effects), and Bypass (explicit trusted
  workspace) modes.
- Interactive, automatic, or deny approval policies, including per-scope
  `changes`, `execution`, and `evidence` decisions.
- Scoped `AGENTS.md` rules, explicit references, incremental context indexing,
  context search/show/complete, compaction, and health diagnostics.
- Validated skills and lifecycle hooks with bounded prompt text, quotas, and
  no ability to bypass the execution boundary.

## Sessions, review, and interfaces

- Durable sessions, checkpoints, pause/resume/cancel/Esc controls, session
  listing/tree/import, status, diff, and hash-checked transaction undo.
- Evidence-driven review covering secrets, forbidden paths, suspicious
  commands, Python syntax, tests, plans, hooks, and transaction state.
- Review artifact export and verification bound to the workspace and hashes.
- Human REPL plus strict JSON/JSONL CLI, `rpc.describe`, Python embedding,
  and a small Node JSONL client with shared contracts.
- Local telemetry policy and redacted audit records; credentials stay in
  environment variables or ignored local state.

## Reproducible checks

```powershell
uv run pytest -q
uv run forgecode doctor
uv run forgecode tools
uv run forgecode provider health
uv run forgecode --workspace $demo run --demo --auto-approve
```

Use the full commands and expected evidence in `demo-script.md`; run the
focused test modules associated with a changed subsystem before the complete
suite.
