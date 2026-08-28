# RPC and embedding contract

ForgeCode exposes a bounded JSONL protocol (`schema_version: 1`) shared by the
CLI, Python `forgecode.embed` helpers and the Node SDK. Every request may carry
an application `id`; replaying an id returns the original response without
reapplying a control operation. Replay memory is bounded to 1024 ids.

## Session lifecycle

`session.open` creates an in-memory handle bound to a validated workspace and
`plan`/`act` mode. `session.status` reads state, `session.events` returns the
last 100 sequenced control events, and `session.cancel`, `session.pause`, and
`session.resume` append one monotonic sequence event. `session.close` revokes
the handle. `session.run` requires a live handle and inherits its workspace and
mode; unknown or malformed handles fail closed with `invalid_request`.
`session.approval` accepts only a boolean decision and records either a running
or approval-denied state, allowing clients to complete an explicit approval
handshake without bypassing CLI policy.

The handle registry is deliberately bounded by request validation and does not
create a second execution loop. Production execution remains owned by the
existing AgentLoop/RunService path.

Provider health is offline by default. `provider health --probe` is an explicit
opt-in reachability check, bounded to a short HEAD request and never including
credential values; offline policy or missing configuration prevents the
request and returns a structured reason.

## Safety and compatibility

Prompts, paths, provider names and environment-variable names are bounded and
newline-safe. JSONL output preserves the CLI envelope, request id and method;
stderr remains diagnostic-only. Clients must treat non-zero `exit_code`,
`ok=false`, and `recovery_required` as non-success states.
