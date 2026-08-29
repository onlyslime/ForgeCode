# Changelog

## v0.7.0 — 2026-08-29

- Full regression gate now passes: 477 tests passed, with 8 Windows
  symlink-privilege skips and two collection warnings.
- Streaming providers now expose validated text deltas to the interactive
  renderer, making assistant prose appear progressively while preserving the
  complete provider response and tool-call validation.
- Completed interactive runs now summarize the actual files changed by
  write/patch tool results, alongside verification and timing metrics.
- Human interactive sessions now emit a bounded five-second working heartbeat
  during provider waits; machine JSON/JSONL output is unchanged.
- `/tools` machine envelopes now include stable capability categories matching
  the human-readable inventory.
- Quality `diagnostics` is classified as evidence in both human and machine
  tool inventories, matching its verification role.
- Restored machine interactive result emission while keeping terminal redraw
  escape sequences out of JSON/JSONL transports.
- `/status` now reports elapsed seconds for an active interactive run, making
  long provider work visible without exposing internal process details.
- Active `/status` snapshots now also expose the current workflow phase and
  completed tool-step count for live progress dashboards.
- Provider retries and failed attempts now appear in the human timeline with
  bounded attempt/category details, while machine audit events remain intact.
- Automatic context compaction now preserves its evidence summary as a
  high-priority system message when fitting the next provider request.
- Provider request identities retain zero-based per-turn suffixes for
  compatibility with existing audit consumers while the UI remains 1-based.
- Interactive Act/Bypass sessions now ask once whether the current workspace
  should be trusted for side effects; declining keeps the session read-only.
- Machine-readable chat output no longer receives terminal redraw escape codes.
- Added grouped, human-readable `/tools` output for read-only, change,
  execution, and evidence capabilities.
- Refined completed-run summaries so verification, elapsed time, and tool-step
  metrics appear together as a clear outcome card.
- Opened the presentation-focused release line for a polished, legible
  demonstration of ForgeCode's normal agent workflow.

## v0.6.3 — 2026-08-29

- Added an npm distribution wrapper that launches a bundled native ForgeCode
  binary, so installed users can run `fcc` without Python or uv.
- Added the Windows x64 build pipeline and package layout for future platform
  binaries; the initial package is published as `@onlyslime/forgecode` because
  the unscoped `forgecode` name is already owned by another project.

## v0.6.2 — 2026-08-29

- Consolidated the historical root changelog into `docs/CHANGELOG.md`.
- Removed the duplicate root changelog and updated documentation references to
  the single canonical history location.
- Reworked public documentation into a concise English README with a linked
  standalone Chinese README, plus a compact assessment handoff.
- Documented `/login` as the primary connection path and removed the unused
  `.env.example` template from the public setup flow.

## v0.6.1 — 2026-08-29

- Completed repository cleanup rules for local source audits and vendor clones.
- Confirmed runtime state, caches, environments, temporary files, and private
  goal/strategy data remain ignored and are not release artifacts.

## v0.6.0 — 2026-08-29

- Started the整理-focused release line. This version entry only establishes
  the release baseline; functional cleanup changes will be added separately.

## v0.5.11 — 2026-08-29

- Disabled fragile SSE negotiation for DeepSeek endpoints and use the bounded
  JSON response path, preventing incomplete tool-call argument failures.

## v0.5.10 — 2026-08-29

- Simplified interactive login to exactly three prompts: endpoint URL, model
  ID, and API key; provider adapter is inferred internally from the URL.

## v0.5.9 — 2026-08-29

- Simplified interactive connection to explicit manual Provider ID, endpoint,
  model ID, and API key entry with no preset values or model guesses.

## v0.5.8 — 2026-08-29

- Replaced the full-screen provider dialog with an inline bounded overlay so
  the chat background is not repainted blue.
- Provider and live model choices remain separated and cancellable.

## v0.5.7 — 2026-08-29

- Model selection now follows OpenCode's live `models.opencode.ai/api.json`
  catalog and omits deprecated entries.
- Removed stale hard-coded model choices from the interactive picker; offline
  use falls back to an explicit custom model ID.

## v0.5.6 — 2026-08-29

- Replaced the blue default provider dialog with a dark themed selector.
- Added a second model picker with verified provider model IDs and an explicit
  custom-model option; no model is silently guessed.
- Based the picker flow on OpenCode's provider/model separation and cancellation
  behavior.

## v0.5.5 — 2026-08-29

- `/login` and `/connect` now open a modal provider picker in interactive TTYs.
- Connection prompts are flushed in the correct order and models require an
  explicit value instead of silently accepting a default.

## v0.5.4 — 2026-08-29

- Unified interactive `/login` with `/connect` and added an explicit provider
  selection screen.
- Models are no longer silently defaulted; users must enter the model while a
  recommendation is shown.
- Fixed bypass-mode interactive checkpoints and rendered command feedback.

## v0.5.3 — 2026-08-29

- Consolidated provider setup around `/connect`; `/login` is now a compatibility
  alias with guidance to use `/connect`.
- Added built-in provider defaults for OpenAI, Anthropic, Google, DeepSeek,
  OpenRouter, Groq, Mistral, xAI, and Ollama, including endpoint, credential
  environment variable, and recommended model hints.
- OpenAI-compatible adapters can now target the expanded provider catalog.

## v0.5.2 — 2026-08-29

- Fixed human-readable `/status` output; it now shows mode, run ID, last state,
  transaction count, verification state, and worker queue status.

## v0.5.1 — 2026-08-29

- Fixed the interactive TTY prompt to catch invalid slash commands and render
  a recoverable error instead of terminating the chat process.

## v0.5.0 — 2026-08-29

- Started the repair-focused release line. This release only updates the
  version and records the theme; command-by-command fixes will follow after
  real interactive reproduction.

## v0.4.4 — 2026-08-29

- Added controlled background process tools: `run_background`,
  `process_status`, `poll_process`, and `kill_process`.
- Background tasks have ForgeCode-owned IDs, bounded incremental output, status
  and exit metadata, cancellation, approval, and command risk checks.

## v0.4.3 — 2026-08-29

- Added read-only `git_log` for recent commit history.
- Added approval-gated `git_commit`; it refuses plan mode, cancellation, and
  empty unstaged commits.

## v0.4.2 — 2026-08-29

- Added `read_range` for precise bounded line-range inspection.
- Added `list_symbols` for lightweight source structure discovery.
- Added `file_metadata` for encoding, size, line count, mtime, and SHA-256
  inspection.

## v0.4.1 — 2026-08-29

- Added `find_files` for bounded glob discovery.
- Added `test` for approved, bounded project test execution.
- Added `diagnostics` for approved compile/lint-style checks with structured
  exit results.

## v0.4.0 — 2026-08-29

- Started the tools-focused release line with dedicated read-only `git_status`
  and `git_diff` tools for auditable repository inspection and review.
- The tools are workspace-scoped, bounded, and available through the normal
  registry and `/tools` inventory.

## v0.3.7 — 2026-08-29

- Added the interactive `/tools` command with descriptions and mode-aware
  availability, plus slash completion support.

## v0.3.6 — 2026-08-29

- Added a compact startup status card with mode, model, tool count, and
  workspace state.
- Added visible Understand/Inspect/Modify/Verify phase separators and numbered
  tool steps to the human timeline.
- Added a structured `Completed` summary with verification status and tool-step
  count.

## v0.3.5 — 2026-08-29

- Improved the human timeline with bounded file-content previews (line
  numbers), command/search output panels, truncation hints, and cumulative tool
  step counts in the final `Worked for …` summary.

## v0.3.4 — 2026-08-29

- Enabled SSE streaming by default for profiles using `streaming = "auto"`;
  providers without stream transport still fall back to normal completion.
  This makes supported interactive providers visibly responsive without
  changing machine-output contracts.

## v0.3.3 — 2026-08-29

- Improved human-readable task timelines with numbered assistant turns,
  elapsed time, and cumulative tool-step counts.

## v0.3.2 — 2026-08-29

- Added immediate assistant progress events before each model turn, so
  multi-step tasks visibly show analysis and continuation instead of appearing
  silent between tool calls.

## v0.3.1 — 2026-08-29

- Bound standalone `Esc` in the prompt UI to cancel the active task while
  keeping the chat session and input buffer available.

## v0.3.0 — 2026-08-29

- Started the 0.3 release line with the current interactive launcher modes,
  slash-command completion, live progress display, and robust tool-call
  context handling.

## v0.2.12 — 2026-08-29

- Added `fcc --plan` and `fcc --act` launch shortcuts alongside
  `fcc --bypass`.
- Fixed `/clear` to flush the terminal clear sequence immediately and return
  a structured result for interactive transports.

## v0.2.11 — 2026-08-29

- Added `fcc --bypass` to launch directly in bypass mode.
- Added interactive slash-command completion; typing `/m` suggests commands
  such as `/mode` and `/model`.

## v0.2.10 — 2026-08-29

- Added a conversational execution contract: the model is instructed to give a
  brief plan before tools, concise progress updates during multi-step work, and
  a final summary with verification and remaining limitations.

## v0.2.9 — 2026-08-29

- Interactive chat now renders assistant progress messages as soon as each
  model turn completes, instead of showing only the final response. Tool
  progress remains visible and machine JSON output is unchanged.

## v0.2.8 — 2026-08-29

- Replaced the `fc` launcher with `fcc` to avoid PowerShell's built-in alias.

## v0.2.7 — 2026-08-29

- Added the `fc` executable shortcut, which opens chat directly without
  arguments.
- Fixed runtime duration tracking by importing the monotonic clock module.

## v0.2.6 — 2026-08-29

- Added elapsed runtime markers to interactive progress events and a final
  `Worked for …` duration in completed chat responses.

## v0.2.5 — 2026-08-29

- Removed the default fixed 12-step AgentLoop cap. Runs now continue until the
  model finishes, fails, is cancelled, or an explicit `max_steps` is set.

## v0.2.4 — 2026-08-29

- Added dark-background file previews with unified red deletion and green
  addition lines during write operations.

## v0.2.3 — 2026-08-29

- Added inline previews for write and patch operations in interactive progress,
  with green additions and red deletions.

## v0.2.2 — 2026-08-29

- Improved live progress labels with file paths and command text, including
  distinct success and failure markers for tool and verification events.

## v0.2.1 — 2026-08-29

- Added live human-readable progress events for interactive runs, including
  tool calls, successful/failed results, and verification status.
- Progress lines use cyan, green, and red markers and remain above the input
  area.

## v0.2.0 — 2026-08-29

- Promoted the stable multiline, fixed-footer terminal chat interface to the
  0.2 feature release.
- Enter submits input, Shift+Enter inserts newlines, and multiline rendering
  remains compatible with the supported prompt-toolkit callback signature.

## v0.1.6 — 2026-08-29

- Fixed multiline prompt rendering on prompt_toolkit versions that pass the
  wrap-count argument to continuation callbacks.

## v0.1.5 — 2026-08-29

- Styled multiline continuation rows so the entire input buffer keeps the
  dark input background.
- Enter submits; the terminal's Shift+Enter escape sequence inserts a newline.

## v0.1.4 — 2026-08-29

- Fixed chat startup failure caused by an unsupported prompt-toolkit
  `s-enter` binding; Ctrl-J now inserts a newline while Enter submits.

## v0.1.3 — 2026-08-29

- Enter now submits chat input; Shift+Enter inserts a newline in the multiline
  buffer.
- Added explicit dark styling for the fixed input area.

## v0.1.2 — 2026-08-29

- Added a prompt-toolkit chat surface with a fixed bottom multiline input
  buffer and safe asynchronous output repainting.
- Pasted multiline content is submitted as one prompt when Enter is pressed.

## v0.1.1 — 2026-08-29

- Published the next patch release after validating interactive bypass-mode
  file creation with a short `hello.txt` task.
- Keeps long provider requests unchanged for a follow-up investigation; those
  requests may still hit the configured provider deadline.
- Verification: `uv run python -m compileall -q src`; `forgecode doctor`.

## v0.1.0 — 2026-08-29

- Declares the first minor release milestone for the runnable CLI harness.
- Consolidates the provider connection flow, human terminal presentation, and
  safety/audit boundaries delivered through the v0.0.x development series.
- Verification: `forgecode doctor --json`, Python compile check, review scan,
  and interactive CLI smoke checks.

## v0.0.36 — 2026-08-29

- Stabilized explicit cancellation while a legacy provider is still
  unwinding: the loop reports `cancelled` unless an unresolved worker is tied
  to a pending side-effecting action requiring recovery.
- Preserved unresolved-provider audit events and recovery semantics for
  deadlines and side-effect conflicts.
- Verification: cancellation hardening regression and recovery tests.

## v0.0.44 — 2026-08-29

- Added Python embedding `login` for provider/profile credential references;
  only environment-variable names cross the RPC boundary.
- Verification: embedding contract, compile, doctor, and full regression tests.

## v0.0.43 — 2026-08-29

- Added provider discovery and health helpers to Node/Python SDKs, exposing
  the existing `provider.list`/`provider.health` RPC contract programmatically.
- Verification: SDK, embedding, RPC, doctor, compile, and regression tests.

## v0.0.42 — 2026-08-29

- Added Python embedding `config_profiles` provider/model discovery, bringing
  configuration introspection parity with the Node SDK and RPC CLI.
- Verification: embedding contract, RPC, compile, and doctor checks.

## v0.0.41 — 2026-08-29

- Added Node SDK convenience controls `sessionCancel`, `sessionPause`, and
  `sessionResume`, matching Python embedding and the RPC session protocol.
- Verification: Node SDK parity smoke, full regression, and compile checks.

## v0.0.40 — 2026-08-29

- Added Python embedding `session_inspect` and `session_events` read APIs for
  durable-session metadata and incremental audit retrieval.
- Verification: embedding contract, compile, and RPC envelope tests.

## v0.0.39 — 2026-08-29

- Added Python embedding `session_open` and `session_run` helpers so
  programmatic clients can create and drive durable sessions through the same
  RPC envelope and workspace/mode/prompt validation as Node and CLI clients.
- Verification: embedding contract, compile, and RPC envelope tests.

## v0.0.38 — 2026-08-29

- Added Python embedding session controls (`session_cancel`, `session_pause`,
  `session_resume`, `session_approval`) with bounded workspace validation and
  standard RPC envelopes, bringing Python parity with Node and CLI controls.
- Verification: embedding contract and compile checks.

## v0.0.37 — 2026-08-29

- Enforced canonical workspace binding on RPC session controls. A caller
  supplying a workspace must match the workspace captured at `session.open`;
  mismatches are rejected before session control is applied.
- Verification: RPC session lifecycle and workspace mismatch regression tests.

## v0.0.35 — 2026-08-29

- Added `--mode plan|act` to `config policy` and matching RPC/Node/Python
  parameters, so policy explanations reflect runtime mode overrides.
- Verification: policy, RPC, embed, compile, and diff contract checks.

## v0.0.34 — 2026-08-29

- Added read-only `config policy` / `config.policy` permission explanations,
  including per-tool runtime narrowing, mode, approval, and trust reasons.
- Added Node `configPolicy()` and Python `config_policy_embedded()` helpers.
- Verification: CLI policy contract, RPC, Node, and Python embedding tests.
- Policy output now includes redacted rule source metadata (fingerprint, path,
  scope, priority, digest, diagnostics) without including rule text.
- Added direct RPC and Python embedding contract coverage for policy parameter
  mapping, rule redaction, and boolean/size validation.

## v0.0.33 — 2026-08-29

- Fixed Node `sessionList()` to map workspace, lifecycle state, and limit into
  RPC parameters instead of silently treating them as process options.
- Verification: Node SDK contract and RPC session tests.
- Corrected Node `sessionTree()` direct workspace/limit options to map into
  RPC parameters; this compatibility fix remains grouped under v0.0.33.
- Aligned direct workspace options across Node session open/status/result/wait,
  events, control, inspect, and run helpers; grouped under v0.0.33.
- Added Python `session_tree()` and `session_tree_embedded` for parity with the
  CLI, RPC, and Node session-tree discovery contract; grouped under v0.0.33.

## v0.0.32 — 2026-08-29

- Bound RPC `session.tree` discovery to its explicitly supplied canonical
  workspace, matching `session.list` and preventing cross-workspace metadata
  reads from programmatic clients.
- Verification: RPC lifecycle contract tests.

## v0.0.31 — 2026-08-29

- Added Python `session_list_embedded()` helper, completing session discovery
  parity across CLI, RPC, Node, and Python embedding APIs.
- Verification: RPC/CLI contract tests and Python import/compile checks.

## v0.0.30 — 2026-08-29

- Added `session.list` RPC and Node `sessionList` helper with bounded workspace,
  lifecycle-state filtering, and consistent machine envelopes.
- Verification: RPC lifecycle and CLI machine-contract tests.

## v0.0.29 — 2026-08-29

- Added bounded `sessions --state` filtering for scriptable background-session
  orchestration while preserving human and JSON/JSONL envelopes.
- Verification: `tests/test_cli_machine_contract.py` (24 passed).

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
- Added typed closed-session writes and bounded `interactive.closeAndWait()`
  cleanup with terminate fallback.
- Converted malformed Node interactive JSON into typed `process_error` events
  with worker termination instead of uncaught host exceptions.
- Added bounded stderr draining for Node interactive workers to prevent pipe
  backpressure deadlocks; the retained diagnostic tail is exposed read-only.
- Added `AbortSignal` cancellation to Node `invoke`/`invokeStream` with typed
  `cancelled` errors and immediate child termination.
- Cleaned Node abort listeners on every terminal path to prevent long-lived SDK
  hosts from accumulating request references.
- Converted Node interactive child-process spawn failures into typed
  `process_error` events instead of uncaught host exceptions.
- Added Python `session_result()` convenience API to match the Node SDK result
  retrieval contract.
- Exported the Python result helper through the package-level
  `session_result_embedded` API for discoverable embedding use.
- Added bounded `session.wait` RPC plus Node/Python helpers to await background
  runs without polling.
- Exported the Python wait helper through the package-level
  `session_wait_embedded` API for discoverable embedding use.
- Aligned Python `session_wait()` workspace validation with the result helper
  for cross-workspace daemon clients.
- Replaced session wait polling with condition notifications on state changes,
  reducing idle wakeups without changing timeout behavior.
- Corrected `session.wait` to refresh state, sequence, and worker liveness after
  waiting, returning a coherent terminal snapshot.
- Isolated RPC pause/resume now attempt OS-level suspension signals where
  supported and record the applied control mechanism in session events.
- Prevented closing paused RPC handles while their worker may still be alive;
  callers must resume or cancel first.
- Made control/close operations on orphaned `recovery_required` handles fail
  closed until an explicit recovery run reclaims them.
- Exposed validated model profile discovery through the `config.profiles` RPC
  method and Node `configProfiles` helper.
- Made config/provider/doctor RPC workspace selection explicit and canonical,
  with the selected workspace echoed for auditability.
- Kept Act-session cancellation available after trust revocation so active
  workers can always be stopped while new execution remains denied.
- Enforced existing-directory validation for diagnostic RPC workspace
  parameters before invoking downstream CLI code.
- Kept read-only Act `session.result` retrieval available after trust
  revocation for post-incident audit evidence.
- Spool-isolated RPC stdout to bounded temporary storage, preventing large
  provider/tool output from exhausting daemon memory.
- Corrected output truncation handling so successful isolated runs remain
  `completed` rather than being misclassified as failed.
- Prevented closing cancelled handles while their isolated child process is
  still alive, eliminating a teardown/recovery race.
- Added bounded terminate/kill cancellation fallback and auditable termination
  method metadata for isolated RPC workers.
- Included execution mode and explicit `worker_alive=false` in recovered open
  responses for deterministic client recovery decisions.
- Allowed read-only `session.wait` on trust-revoked/recovery handles so clients
  can observe terminal state without re-enabling execution.
- Finalized isolated RPC handles on child-process startup failure with a
  structured process error instead of leaving them stuck in `running`.
- Added read-only `session.result` RPC and Node helper for retrieving bounded
  background run envelopes without polling full status metadata.
- Verification: full regression `456 passed, 8 skipped, 2 warnings`, RPC
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
