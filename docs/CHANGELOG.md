# ForgeCode version updates

This file records user-visible version changes. Small fixes, tests, refactors,
and metadata-only commits remain under the current version and are not listed as
new releases. Verification notes identify the evidence used for each feature
slice.

## v0.0.28 — 2026-08-29

- Added opt-in background RPC session runs. `session.run` with
  `background: true` returns an immediate accepted envelope while the shared
  handle executes on a daemon worker; status/events expose terminal
  completion and concurrent controls can cancel or pause the run.
- Synchronous `session.run` behavior remains backward compatible.
- Recovered orphaned running handles now report `recovery_required` and emit a
  restart event when explicitly reclaimed by a new run.
- Background run envelopes are now retained with a bounded result payload and
  survive session recovery.
- Added opt-in isolated background workers so cancellation can terminate a
  non-cooperative provider process without changing synchronous runs.
- Unified RunService AgentLoop lifecycle callbacks with the privacy-filtered
  telemetry recorder for auditable provider/tool/session families.
- Added a bounded `EmbeddedSession` event queue (`max_events`) to make Python
  embedding backpressure explicit and reject unsafe queue sizes.
- Bounded Node SDK stderr diagnostics and interactive event retention with
  explicit `maxStderrBytes`/`maxEvents` limits.
- Verification: full regression `444 passed, 8 skipped, 2 warnings`, RPC
  lifecycle suite, compile, and CLI/doctor checks.

## v0.0.27 — 2026-08-28

- Synchronized release metadata after the v0.0.26 telemetry retention work.
- Added profile-aware `/login --profile` diagnostics so credential references,
  provider, and model selection are consistent across CLI, RPC, and chat.
- Added RPC event-cursor truncation metadata (`oldest_sequence` and
  `truncated`) for safe disconnect recovery.
- Added an auditable `cancel_requested` marker to RPC session control state.
- Hardened Node streaming JSON parsing to return typed `invalid_json` errors.
- Aligned Python embedding stream parsing with the typed `invalid_json` error
  contract.
- Added telemetry event-family classification and unknown-event audit markers.
- Serialized telemetry append/retention operations for concurrent workers.
- Made act-mode embedded reconnect fail closed when workspace trust is revoked.
- Added trust revalidation at RunService side-effect boundaries during act runs.
- Hardened embedded shutdown with terminate/kill fallbacks and bounded waits.
- Serialized RPC session runs and made cancelled/failed/denied handles terminal
  with explicit recovery semantics.
- Prevented closing active RPC handles before cancellation or completion.
- Restricted provider error parsing to safe scalar messages, preventing nested
  credential fields from entering diagnostics.
- Added stable RPC lifecycle error codes for busy, terminal, and denied states.
- Bounded JSONL RPC request lines to 1 MiB before parsing.
- Documented the bounded request-line contract and `request_too_large` error.
- Verified cancellation markers survive persisted RPC handle recovery.
- Closed embedded worker pipe handles during shutdown to avoid Windows resource
  warnings after forced termination.
- Canonicalized persisted RPC workspaces and bounded request identifiers.
- Normalized Node/Python SDK limits and process/JSON error contracts.
- Verification: targeted RPC/embed/provider/telemetry/cancellation/context/review gate (86 passed), Node
  smoke, Python compile, doctor JSONL, and diff checks.
- Additional hardening: bounded SDK argv/params and request ids, typed process
  and JSON errors, RPC busy/terminal/approval taxonomy, cancellation recovery,
  canonical workspace persistence, trust fail-closed execution, and provider
  error redaction.
- Bounded Python embedding stream requests to JSON objects and 1 MiB payloads,
  aligning client-side validation with the RPC protocol.
- Included persisted state, sequence, and cancellation metadata in recovered
  `session.open` responses.
- Added telemetry export `returned_count` and `truncated` metadata for bounded
  audit exports.
- Applied argv bounds consistently to Node streaming invocation.
- Rejected non-standard NaN/Infinity values in Python embedding stream JSON.
- Normalized writes to exited embedded workers as typed `process_error` failures.
- Added profile/provider credential selectors to the Node login helper.
- Normalized Node child-process spawn failures to typed `process_error` errors.
- Verification: version/import consistency and targeted telemetry checks.

## v0.0.26 — 2026-08-28

- Added bounded local telemetry retention with atomic trimming of old records.
- Verification: telemetry retention, privacy, compile, and diff checks.

## v0.0.25 — 2026-08-28

- Added `telemetry status` and `telemetry export` CLI commands with bounded
  local audit export and explicit offline policy reporting.
- Verification: telemetry CLI tests and doctor smoke check.

## v0.0.24 — 2026-08-28

- Added Python `EmbeddedSession` and Node `interactive()` controls for the
  production chat worker: send, pause, resume, cancel, and quit.
- Verification: embedded worker control and RPC regression tests.

## v0.0.23 — 2026-08-28

- Added RPC methods for `session.inspect`, `session.tree`, and
  `session.export`, with bounded session parameters and Node helpers.
- Verification: RPC method and parameter validation tests.

## v0.0.22 — 2026-08-28

- Added parameterized RPC `run` requests, including bounded prompt, workspace,
  mode, profile, demo, approval, and trust options.
- Verification: JSONL RPC run and malformed-parameter tests.

## v0.0.20 — 2026-08-28

- Added explicit RPC method dispatch with request IDs and Python/Node embedding
  support while retaining the legacy `argv` request shape.
- Verification: RPC request-id, method, and embedding tests.

## v0.0.19 — 2026-08-28

- Expanded RPC method compatibility and method echoing for machine clients.
- Verification: RPC method dispatch tests.

## v0.0.17 — 2026-08-28

- Stabilized RPC method dispatch and preserved legacy provider injection paths.
- Verification: CLI compatibility and provider tests.

## v0.0.16 — 2026-08-28

- Aligned provider capability diagnostics with supported streaming adapters.
- Verification: provider list and protocol tests.

## v0.0.15 — 2026-08-28

- Added provider registry diagnostics via `provider list` for
  OpenAI-compatible, Anthropic, Google, and Ollama adapters.
- Verification: machine-readable provider registry tests.

## v0.0.14 — 2026-08-28

- Added the first Python embedded API and RPC request-id foundation.
- Verification: embedded invocation, compile, and doctor checks.

## v0.0.13 — 2026-08-28

- Completed the CLI harness slice: provider/profile credentials and login,
  provider protocol adapters, trust grant/revoke, offline/telemetry policy,
  Escape cancellation, JSONL RPC, Node SDK, and privacy-aware audit events.
- Verification: full regression gate (`374 passed, 8 skipped`), doctor,
  compile, CLI smoke, and diff checks.

## v0.0.12 — 2026-08-28

- Added monotonic runtime tool narrowing with `--tools`,
  `--exclude-tools`, and `--no-tools`, preserving approval, timeout,
  cancellation, and redaction boundaries.
- Verification: v0.0.12 policy regression suite and CLI machine-contract tests.

## v0.0.11 — 2026-08-28

- Added Pi-inspired `!<command>` and `!!<command>` interactive shortcuts with
  bounded output and distinct model-visible/local audit semantics.

## v0.0.10 — 2026-08-28

- Added the controllable interactive worker with bounded FIFO follow-ups and
  `/pause`, `/resume`, and `/cancel`.

## v0.0.9 — 2026-08-28

- Added durable long-run workflows: bounded context compaction, trajectory
  evaluation, session trees, cloning, and model profiles.

## v0.0.8 — 2026-08-28

- Hardened cancellation, recovery, checkpoint validation, transaction
  evidence, and release acceptance workflows.

## v0.0.7 — 2026-08-28

- Productized extensible local agent workflows with rules, references, plans,
  skills, repository context, and auditable CLI contracts.
