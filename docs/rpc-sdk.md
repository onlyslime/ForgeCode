# RPC and embedding contract

ForgeCode exposes a bounded JSONL protocol (`schema_version: 1`) shared by the
CLI, Python `forgecode.embed` helpers and the Node SDK. Every request may carry
an application `id`; replaying an id returns the original response without
reapplying a control operation. Replay memory is bounded to 1024 ids. Session
handles are bounded to 256 active handles with an eight-hour TTL; expired
handles fail closed instead of accumulating in a long-lived daemon.

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
`session.events` accepts bounded `after` and `limit` cursors and returns
`next_sequence`, so a disconnected client can resume event consumption without
replaying already acknowledged records.
Terminal states are monotonic: a completed, failed, cancelled, or denied
session rejects later pause/resume/cancel/approval requests.

The handle registry is deliberately bounded by request validation and does not
create a second execution loop. Production execution remains owned by the
existing AgentLoop/RunService path.

Provider health is offline by default. `provider health --probe` is an explicit
opt-in reachability check, bounded to a short HEAD request and never including
credential values; offline policy or missing configuration prevents the
request and returns a structured reason.

The Node client raises `ForgeCodeError` for timeout, output-limit, empty
response, invalid JSON, and `ok=false` envelopes. `invoke` accepts bounded
`timeoutMs` and `maxOutputBytes`; `invokeStream` supports the same method/params
RPC transport for programmatic callers.

Python embedding mirrors this contract with `forgecode.embed.ForgeCodeError`.
`invoke(..., raise_for_status=True)` and `stream(..., raise_for_status=True)`
raise typed failures while preserving the original envelope; responses are
bounded by `max_response_bytes`.

`EmbeddedSession.reconnect()` is an explicit bounded recovery operation: it
only starts a replacement worker after the prior process exits, preserves the
workspace/mode binding, and emits `process_reconnected`. It never loops or
silently retries side effects.

## Safety and compatibility

Prompts, paths, provider names and environment-variable names are bounded and
newline-safe. JSONL output preserves the CLI envelope, request id and method;
stderr remains diagnostic-only. Clients must treat non-zero `exit_code`,
`ok=false`, and `recovery_required` as non-success states.
