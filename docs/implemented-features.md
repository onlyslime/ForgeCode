# Implemented capabilities

This inventory summarizes user-visible behavior currently covered by the
runtime and regression suite.

- Provider-neutral model protocol with streaming, retries, deadlines,
  cancellation, and offline deterministic demo mode.
- Workspace-aware listing, reading, search, summaries, multi-file patches,
  atomic writes, and bounded/redacted command output.
- Plan, Act, and Bypass modes with risk classification, approvals, trusted
  workspaces, timeout/output limits, and safe process termination.
- Scoped `AGENTS.md` rules, references, incremental context indexing,
  skills, lifecycle hooks, and context compaction.
- Durable sessions and checkpoints, hash-aware transactions and undo,
  pause/resume/cancel, review, export/verify, and JSONL audit evidence.
- Human REPL plus strict JSON/JSONL CLI, Python embedding, and Node client.

See [`capability-trace.md`](capability-trace.md) for source locations and
reproducible evidence, and [`demo-script.md`](demo-script.md) for an offline
walkthrough.
