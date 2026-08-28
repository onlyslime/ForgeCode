# RPC and embedding contract

ForgeCode exposes a bounded JSONL protocol (`schema_version: 1`) shared by the
CLI, Python `forgecode.embed` helpers and the Node SDK. Every request may carry
an application `id`; replaying an id returns the original response without
reapplying a control operation. Replay memory is bounded to 1024 ids. String
ids are non-empty, newline-safe, and limited to 256 characters. Session
JSONL request lines are capped at 1 MiB; oversized input returns
`request_too_large` before parsing.
handles are bounded to 256 active handles with an eight-hour TTL; expired
handles fail closed instead of accumulating in a long-lived daemon.
Handle metadata is persisted under the workspace's ignored
`.forgecode/rpc-sessions/` directory without prompts or credentials. A new
daemon may explicitly reopen a handle after canonical workspace and mode
validation.
Recovery also rechecks persisted creation time against the eight-hour TTL and
requires current workspace trust for Act handles before returning success.
If a persisted handle was `running` when the daemon disappeared, recovery
returns `recovery_required` rather than claiming a worker still exists. A new
`session.run` explicitly reclaims that handle and emits `recovery_restarted`.
Recovered opens identify the prior execution mode and always report
`worker_alive: false`, because in-process workers cannot survive daemon exit.
Control and close requests against a `recovery_required` handle fail with
`recovery_required`; only an explicit new run may reclaim it.
After Act trust is revoked, cancellation remains permitted so clients can
terminate an active worker; new execution and other lifecycle controls remain
fail-closed with `trust_revoked`.
The read-only `session.result` method remains available after revocation so
operators can inspect the bounded audit outcome.
The persisted record includes only the bounded recent event window and its
sequence, allowing cursor-based event recovery after daemon restart.
Each handle retains only the most recent 512 control/run events. Clients must
use `next_sequence` and treat cursors older than the retained window as a
resynchronization point.

Python `EmbeddedSession` bounds its in-memory event queue (`max_events`,
default 1,024; maximum 100,000). This prevents a slow consumer from causing
unbounded client-side memory growth; callers should poll regularly and use
the persisted session/RPC cursors for durable recovery.
The Python `session_result()` helper mirrors Node `sessionResult()`, validating
the handle/workspace and returning the same `session.result` envelope with the
existing typed error and response-size limits.
It is also exported as `forgecode.session_result_embedded` for callers that
use the package-level embedding API.
The Node `invokeStream` helper similarly caps diagnostic stderr via
`maxStderrBytes` (256 KiB by default), and `interactive` retains only a
bounded `maxEvents` window (2,048 by default).
Node interactive sessions reject writes after `close()` with typed
`process_error`; `closeAndWait(timeoutMs)` provides a bounded graceful quit
with process termination fallback for hosts that need deterministic cleanup.
Malformed JSON emitted by an interactive worker is converted into a bounded
`process_error` event and the worker is terminated, rather than escaping into
the embedding application's event loop.

## Session lifecycle

`session.open` creates an in-memory handle bound to a validated workspace and
`plan`/`act` mode. `session.status` reads state, `session.events` returns the
last 100 sequenced control events, and `session.cancel`, `session.pause`, and
`session.resume` append one monotonic sequence event. `session.close` revokes
the handle. `session.run` requires a live handle and inherits its workspace and
mode; unknown or malformed handles fail closed with `invalid_request`.
Close also removes the persisted recovery record, so a closed handle cannot be
reopened after daemon restart.
`session.approval` accepts only a boolean decision and records either a running
or approval-denied state, allowing clients to complete an explicit approval
handshake without bypassing CLI policy.
Clients may pass `background: true` to `session.run` to receive an immediate
`accepted` envelope while the shared handle remains `running`; status/events
then expose the terminal `run_finished` event and controls can be sent during
execution. The default synchronous response remains unchanged.
For isolated workers, pause/resume also attempt OS-level `SIGSTOP`/`SIGCONT`
when the platform exposes them; the control event records whether a signal or
cooperative fallback was used.
For providers that may block, `isolate: true` runs the request in a killable
child process; `session.cancel` performs a bounded terminate request and the
result is recorded as cancelled. Isolation is opt-in because it has higher
startup cost and still uses the same CLI safety checks.
Isolated stdout is spooled to a temporary file and only a bounded tail is
parsed after exit, preventing large model/tool output from accumulating in
daemon memory; oversized captures are marked `output_truncated`.
This marker is non-fatal when the child exits successfully; the session remains
`completed` and callers can distinguish truncated output from worker failure.
If child-process creation fails, the handle is finalized as `failed` with a
`process_error` event rather than remaining indefinitely `running`.
Completed background runs retain a bounded list of structured CLI envelopes in
`session.status` and in recovered `session.open` responses; oversized output
is represented by a redacted truncation marker.
`session.result` is the equivalent read-only RPC for clients that only need
the retained result payload and terminal metadata.
`session.close` rejects active running handles; clients must cancel or await
completion before revocation so an in-flight worker cannot lose its recovery
metadata.
After cancellation, close also waits for any still-live isolated process to
exit; the handle is not revoked while teardown is in flight.
Cancellation uses a bounded terminate-then-kill fallback for isolated workers;
the resulting `termination` method (`terminate`, `kill`, or `unresolved`) is
recorded in the control event for audit and recovery decisions.
Paused handles are also considered active and cannot be closed until resumed
or cancelled, preventing an orphaned suspended worker.
`session.events` accepts bounded `after` and `limit` cursors and returns
`next_sequence`, so a disconnected client can resume event consumption without
replaying already acknowledged records. Responses also include
`oldest_sequence` and `truncated`; a true `truncated` value means the cursor
predates the bounded retention window, so clients must treat the event history
as incomplete and reacquire a status/review snapshot.
Terminal states are monotonic: a completed, failed, cancelled, or denied
session rejects later pause/resume/cancel/approval requests.
Lifecycle violations use stable error codes: `session_busy` is retryable after
the active run ends, while `session_terminal` and `approval_denied` require a
new handle.
Cancellation responses expose `cancel_requested` so clients can distinguish a
recorded cancellation request from a worker completion event.
The marker is persisted and restored across daemon recovery, preserving the
cancelled terminal decision.

The handle registry is deliberately bounded by request validation and does not
create a second execution loop. Production execution remains owned by the
existing AgentLoop/RunService path.

Provider health is offline by default. `provider health --probe` is an explicit
opt-in reachability check, bounded to a short HEAD request and never including
credential values; offline policy or missing configuration prevents the
request and returns a structured reason.
The `config.profiles` RPC method exposes the same validated profile/model
discovery as the CLI `config profiles` command, allowing SDK clients to select
an explicit profile on their next run without reading local config files.
Configuration, provider, and doctor RPC methods accept a bounded `workspace`
parameter and echo its canonical path in the envelope, so profile discovery is
independent of the daemon's current directory.
The workspace must already exist; missing directories are rejected before the
CLI is invoked.

The Node client raises `ForgeCodeError` for timeout, output-limit, empty
response, process spawn failures, invalid JSON (including malformed stream lines), and `ok=false` envelopes. `invoke` accepts bounded
`timeoutMs` and `maxOutputBytes`; `invokeStream` supports the same method/params
RPC transport for programmatic callers.
The Node `login({ profile, provider, apiKeyEnv })` helper forwards the same
bounded selectors as the CLI and RPC login methods.

Python embedding mirrors this contract with `forgecode.embed.ForgeCodeError`.
`invoke(..., raise_for_status=True)` and `stream(..., raise_for_status=True)`
raise typed failures while preserving the original envelope; responses are
bounded by `max_response_bytes`. Malformed JSON received during `stream()` is
reported as `ForgeCodeError(code="invalid_json")`, matching the Node SDK.

`EmbeddedSession.reconnect()` is an explicit bounded recovery operation: it
only starts a replacement worker after the prior process exits, preserves the
workspace/mode binding, and emits `process_reconnected`. It never loops or
silently retries side effects. Act-mode reconnect also revalidates persisted
workspace trust and raises `ForgeCodeError(code="trust_required")` when trust
has been revoked.
Writes to a closed embedded worker raise `ForgeCodeError(code="process_error")`
instead of leaking platform-specific pipe exceptions.
Closing an embedded session uses bounded graceful shutdown, then terminate and
kill fallbacks with waits, so callers do not retain an uncollected child process.

## Safety and compatibility

Prompts, paths, provider names and environment-variable names are bounded and
newline-safe. JSONL output preserves the CLI envelope, request id and method;
stderr remains diagnostic-only. Clients must treat non-zero `exit_code`,
`ok=false`, and `recovery_required` as non-success states.
