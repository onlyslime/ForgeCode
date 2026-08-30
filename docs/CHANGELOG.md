# Changelog

## v0.8.152 — 2026-08-31

- **Tool message serialization:** reject non-JSON-safe metadata instead of coercing arbitrary objects with `default=str`; emit a bounded safe marker for model context.
- **Verification:** agent-edge tests, compile checks, and manual metadata serialization inspection passed.

## v0.8.151 — 2026-08-31

- **Tool result contract:** require extension tools to return a boolean `ToolResult.ok` flag, preventing truthy string values from corrupting execution and audit semantics.
- **Verification:** security-edge tests, compile checks, and manual malformed-result inspection passed.

## v0.8.150 — 2026-08-31

- **Request body bound:** custom provider transport requests now reject bodies larger than 4 MiB before JSON parsing.
- **Verification:** provider tests, compile checks, and manual oversized-request inspection passed.

## v0.8.149 — 2026-08-31

- **Provider text strictness:** response translators now reject non-string text/content fields instead of coercing arbitrary JSON values into assistant text.
- **Verification:** provider tests, compile checks, and manual malformed-content inspection passed.

## v0.8.148 — 2026-08-31

- **Transport scheme safety:** restrict custom provider transport URLs to HTTP/HTTPS schemes before rewriting or dispatch.
- **Verification:** provider tests, compile checks, and manual non-HTTP URL inspection passed.

## v0.8.147 — 2026-08-31

- **Transport response bound:** cap custom transport response bodies at 4 MiB before parsing, matching provider safety limits and reducing memory pressure.
- **Verification:** provider tests, compile checks, and manual oversized-body inspection passed.

## v0.8.146 — 2026-08-31

- **Common request validation:** Ollama requests now enforce the same object-only message list contract as Anthropic and Google translations.
- **Verification:** provider tests, compile checks, and manual malformed-message inspection passed.

## v0.8.145 — 2026-08-31

- **Provider tool conversion:** validate nested `function` objects in Anthropic and Google request translation, preventing malformed schemas from raising attribute errors.
- **Verification:** provider tests, compile checks, and manual nested-schema inspection passed.

## v0.8.144 — 2026-08-31

- **Request header safety:** custom transport request headers now enforce bounded, non-empty string keys and values without control characters.
- **Verification:** provider tests, compile checks, and manual malformed-header inspection passed.

## v0.8.143 — 2026-08-31

- **Response body strictness:** provider factory response translation now requires bytes-like bodies, matching request transport contracts.
- **Verification:** provider tests, compile checks, and manual malformed-response inspection passed.

## v0.8.142 — 2026-08-31

- **Transport URL safety:** reject control characters in custom transport URLs before provider-specific rewriting or network dispatch.
- **Verification:** provider tests, compile checks, and manual malformed-URL inspection passed.

## v0.8.141 — 2026-08-31

- **HTTP status strictness:** custom transport results now require an integer status code, rejecting floats and strings that could be silently coerced into valid codes.
- **Verification:** provider tests, compile checks, and manual status coercion inspection passed.

## v0.8.140 — 2026-08-31

- **Transport body strictness:** custom transport results now require bytes-like response bodies instead of accepting implicit integer-to-zero-byte conversions.
- **Verification:** provider tests, compile checks, and manual transport coercion inspection passed.

## v0.8.139 — 2026-08-31

- **Transport request boundary:** validate URL and header mapping types before provider-specific rewriting, preventing malformed custom calls from leaking attribute errors.
- **Verification:** provider tests, compile checks, and manual malformed-header inspection passed.

## v0.8.138 — 2026-08-31

- **Transport body validation:** reject non-byte request bodies before decoding, converting malformed custom transport inputs into clear `ValueError`s.
- **Verification:** provider tests, compile checks, and manual malformed-body inspection passed.

## v0.8.137 — 2026-08-31

- **Google request validation:** malformed non-list or non-object tool schemas are now rejected instead of silently dropped during provider translation.
- **Verification:** provider tests, compile checks, and manual malformed-tool inspection passed.

## v0.8.136 — 2026-08-31

- **Transport header validation:** constrain custom transport response headers to bounded string keys and values without control characters.
- **Verification:** provider tests, compile checks, and manual malformed-header inspection passed.

## v0.8.135 — 2026-08-31

- **Transport result validation:** normalize and bound status, body, and headers returned by custom transports, converting malformed values into clear `ValueError`s.
- **Verification:** provider tests, compile checks, and manual malformed-transport inspection passed.

## v0.8.134 — 2026-08-31

- **Ollama response validation:** reject non-object messages and malformed tool-call arrays before provider translation.
- **Verification:** provider tests, compile checks, and manual malformed-message inspection passed.

## v0.8.133 — 2026-08-31

- **Google response validation:** validate candidates, candidate content, and parts before translation, preventing leaked index/attribute errors on malformed responses.
- **Verification:** provider tests, compile checks, and manual malformed-candidate inspection passed.

## v0.8.132 — 2026-08-31

- **Anthropic response validation:** reject non-list or non-object content blocks before provider translation, avoiding leaked attribute errors on malformed responses.
- **Verification:** provider tests, compile checks, and manual malformed-content inspection passed.

## v0.8.131 — 2026-08-31

- **Transport response validation:** provider factory adapters now reject malformed or non-object response bodies before provider-specific field access.
- **Verification:** provider tests, compile checks, and manual malformed-response inspection passed.

## v0.8.130 — 2026-08-31

- **Transport request validation:** provider factory translation now rejects malformed JSON bodies and non-object message entries before field access.
- **Verification:** provider tests, compile checks, and manual malformed-request inspection passed.

## v0.8.129 — 2026-08-31

- **Provider factory validation:** Anthropic translation now rejects non-object tool schemas before field access, avoiding leaked attribute errors from malformed requests.
- **Verification:** provider tests, compile checks, and manual malformed-tool inspection passed.

## v0.8.128 — 2026-08-31

- **Outbound description bounds:** wrapped and neutral tool schemas now reject oversized or control-character descriptions before network serialization.
- **Verification:** provider tests, compile checks, and manual malformed-description inspection passed.

## v0.8.127 — 2026-08-31

- **Outbound schema field validation:** reject malformed wrapped function descriptions and parameter objects before request serialization.
- **Verification:** provider tests, compile checks, and manual malformed-schema inspection passed.

## v0.8.126 — 2026-08-31

- **Wrapped schema validation:** validate function names inside already-wrapped OpenAI tool schemas, preventing malformed names from bypassing outbound protocol checks.
- **Verification:** provider tests, compile checks, and manual wrapped-schema inspection passed.

## v0.8.125 — 2026-08-31

- **Outbound schema validation:** provider request construction now rejects non-object tool schemas with a typed protocol error instead of leaking attribute errors.
- **Verification:** provider regression tests, compile checks, and manual malformed-schema inspection passed.

## v0.8.124 — 2026-08-31

- **Extension definition safety:** reject all ASCII control characters in tool names and descriptions, keeping registered extension metadata safe for logs and protocol payloads.
- **Verification:** security-edge regression tests, compile checks, and manual registration checks passed.

## v0.8.123 — 2026-08-31

- **Usage metadata safety:** reject control-character usage field names in provider parsing and provider-neutral validation before they reach logs or telemetry.
- **Verification:** provider tests, compile checks, and manual malformed-usage inspection passed.

## v0.8.122 — 2026-08-31

- **Streaming index validation:** reject negative tool-call fragment indexes before assembly, preventing malformed call ordering state.
- **Verification:** provider regression tests, compile checks, and manual malformed-stream inspection passed.

## v0.8.121 — 2026-08-31

- **Neutral protocol validation:** custom providers now receive the same control-character and length checks for tool IDs, tool names, and tool-result correlation IDs as concrete adapters.
- **Verification:** provider tests, compile checks, and manual constructed-response checks passed.

## v0.8.120 — 2026-08-31

- **Regression correction:** fixed the malformed-ID test fixture to exercise an actual newline and verified the new provider guard.

## v0.8.119 — 2026-08-31

- **Tool-call ID safety:** reject empty, oversized, or control-character IDs in synchronous and streaming provider responses to preserve log/session correlation integrity.
- **Verification:** provider tests, compile checks, and manual malformed-ID inspection passed.

## v0.8.118 — 2026-08-31

- **Streaming protocol guard:** reject non-object SSE events during chat-stream assembly with a structured protocol error.
- **Verification:** provider regression tests and compile checks passed.

## v0.8.117 — 2026-08-31

- **Tool-name log safety:** reject oversized or control-character tool names before lookup, preventing malformed model data from polluting structured logs.
- **Verification:** command-bound regression tests and compile checks passed.

## v0.8.116 — 2026-08-31

- **Provider payload guard:** direct chat-completion parsing now reports a protocol error for non-object JSON payloads, matching the HTTP adapter contract.
- **Verification:** provider regression test and compile checks passed.

## v0.8.115 — 2026-08-31

- **Tool name validation:** malformed or empty tool-call names now return a structured error instead of triggering unhashable-key or lookup failures.
- **Verification:** command-bound regression tests and compile checks passed.

## v0.8.114 — 2026-08-31

- **Tool argument key validation:** reject non-string JSON object keys before schema validation, returning a structured `invalid_arguments` error instead of raising a sorting exception.
- **Verification:** targeted command-bound tests and compile checks passed.

## v0.8.113 — 2026-08-31

- Treat `null` values for schema-required tool fields as missing at the
  registry boundary, producing consistent structured validation errors.
- Verification: null-required-field regression, manual dispatch check,
  compileall, and diff checks passed.

## v0.8.112 — 2026-08-31

- Enforced schema `required` fields at the `ToolRegistry` boundary, returning
  structured missing-field errors before tool dispatch.
- Verification: required/unknown field regressions, manual registry checks,
  compileall, and diff checks passed.

## v0.8.111 — 2026-08-31

- Enforced `additionalProperties: false` at the registry execution boundary,
  rejecting unknown tool-call fields before tool dispatch.
- Verification: registry unknown-field regression, manual dispatch check,
  compileall, and diff checks passed.

## v0.8.110 — 2026-08-31

- Added object-argument validation to `repository_map`, aligning runtime
  behavior with its published schema and preventing leaked attribute errors.
- Verification: invalid-input regression, manual API check, compileall, and
  diff checks passed.

## v0.8.109 — 2026-08-31

- Hardened code-understanding source selection against symlink/junction
  aliases, preventing static tools from following redirected files.
- Verification: alias-path regression, manual source-selection check,
  compileall, and diff checks passed.

## v0.8.108 — 2026-08-31

- Added object-argument validation to `workspace_summary`, aligning the
  read-only workspace inspection tool with the common input contract.
- Verification: invalid-input regression, manual API check, compileall, and
  diff checks passed.

## v0.8.107 — 2026-08-31

- Added object-argument validation to `lsp_status`, aligning the read-only
  discovery tool with the repository-wide tool input contract.
- Verification: invalid-input regression, manual API check, compileall, and
  diff checks passed.

## v0.8.106 — 2026-08-31

- Added strict string validation for optional code-understanding `path`
  filters, preventing non-string values from leaking path resolution errors.
- Verification: invalid-path regression, manual API check, compileall, and
  diff checks passed.

## v0.8.105 — 2026-08-31

- Stopped code-understanding source scans immediately at the 500-file bound,
  avoiding needless traversal of the remainder of large workspaces.
- Verification: 510-file bounded-scan regression, manual traversal check,
  compileall, and diff checks passed.

## v0.8.104 — 2026-08-31

- Centralized object-argument validation in the shared filesystem helper,
  hardening all code-understanding and metadata tools against malformed calls.
- Verification: six-tool invalid-input regression, manual API checks,
  compileall, and diff checks passed.

## v0.8.103 — 2026-08-31

- Synchronized the `repository_map` JSON schema with its runtime budget
  bounds (`minimum: 256`, `maximum: 100000`).
- Verification: schema-contract regression, targeted tests, compileall, and
  diff checks passed.

## v0.8.102 — 2026-08-31

- Added a 100,000-character upper bound to `repository_map.budget_chars`,
  preventing unbounded context allocation from model-supplied requests.
- Verification: oversized-budget regression, manual boundary check,
  compileall, and diff checks passed.

## v0.8.101 — 2026-08-31

- Rejected newline-bearing Git worktree `start_point` refs to prevent
  approval and command-output injection through malformed metadata.
- Verification: malformed-ref regression, manual API check, compileall, and
  diff checks passed.

## v0.8.100 — 2026-08-31

- Restricted worktree ownership paths to safe workspace-relative paths,
  rejecting absolute, empty, and traversal components in metadata.
- Verification: malformed-path regressions, manual parser checks, compileall,
  and diff checks passed.

## v0.8.99 — 2026-08-31

- Added object-argument validation to `git_worktrees`, completing consistent
  malformed-input handling across Git worktree tools.
- Verification: four-tool boundary regression, manual API check, compileall,
  and diff checks passed.

## v0.8.98 — 2026-08-31

- Rejected newline-bearing worktree ownership metadata fields to prevent
  forged records from injecting lines into worktree output and audit text.
- Verification: malformed metadata regression, manual parser check,
  compileall, and diff checks passed.

## v0.8.97 — 2026-08-31

- Enforced string validation for the Git diff `path` argument, preventing
  implicit coercion of numbers or objects into filesystem paths.
- Verification: path-boundary regression, manual API check, compileall, and
  diff checks passed.

## v0.8.96 — 2026-08-31

- Enforced strict boolean validation for `git_worktree_remove.force`,
  preventing values such as `"false"` from enabling destructive `--force`.
- Verification: non-boolean force regression, manual API check, compileall,
  and diff checks passed.

## v0.8.95 — 2026-08-31

- Added object-argument validation to Git worktree create, remove, and
  reconcile tools, preventing malformed direct calls from leaking exceptions.
- Verification: three-tool boundary regression, manual API checks, compileall,
  and diff checks passed.

## v0.8.94 — 2026-08-31

- Enforced strict boolean validation for Git status `porcelain` and diff
  `staged` flags, preventing truthiness-based command selection.
- Verification: non-boolean flag regression, manual API checks, compileall,
  and diff checks passed.

## v0.8.93 — 2026-08-31

- Added object-argument validation to `git_commit`, preventing malformed
  direct calls from leaking attribute errors before approval handling.
- Verification: commit-tool boundary regression, manual API check, compileall,
  and diff checks passed.

## v0.8.92 — 2026-08-31

- Added object-argument validation to Git status, diff, and log inspection
  tools, preventing leaked attribute errors on malformed direct calls.
- Verification: three-tool invalid-input regression, manual API checks,
  compileall, and diff checks passed.

## v0.8.91 — 2026-08-31

- Serialized background state snapshots with the manager's re-entrant lock,
  preventing inconsistent `_items`/`_stale` reads during concurrent persistence.
- Verification: concurrent persistence regression, background tests,
  compileall, and diff checks passed.

## v0.8.90 — 2026-08-31

- Added consistent object-argument validation to all core filesystem tools,
  preventing leaked attribute errors on malformed direct calls.
- Verification: four-tool invalid-input regression, manual API checks,
  compileall, and diff checks passed.

## v0.8.89 — 2026-08-31

- Converted background process startup failures into structured tool results,
  preventing `run_background` from leaking spawn exceptions into the agent
  loop.
- Verification: injected startup failure regression, manual tool invocation,
  compileall, and diff checks passed.

## v0.8.88 — 2026-08-31

- Hardened `kill_process` against a process-exit race, returning structured
  `already_exited` or `termination_failed` results instead of leaking OS
  exceptions.
- Verification: injected `ProcessLookupError` regression, manual termination
  race check, compileall, and diff checks passed.

## v0.8.87 — 2026-08-31

- Protected stale background-task lookups with the manager lock, preventing
  races between snapshot reads and concurrent task cleanup.
- Verification: concurrent unknown-task snapshot regression, manual stress
  check, compileall, and diff checks passed.

## v0.8.86 — 2026-08-31

- Enforced task-ID validation in public `ProcessManager.get()` and
  `snapshot()` APIs, preventing unhashable or newline-bearing values from
  leaking internal errors or ambiguous lookups.
- Verification: manager API boundary regression, background tests, compileall,
  and diff checks passed.

## v0.8.85 — 2026-08-31

- Preserved command risk metadata on quality-tool approval-denied and
  cancellation results, improving auditability of early exits.
- Verification: denial metadata regression, manual approval-path check,
  compileall, and diff checks passed.

## v0.8.84 — 2026-08-31

- Enforced non-empty, newline-safe string task IDs across process status,
  polling, and termination tools instead of coercing arbitrary values.
- Verification: task-ID boundary regression, manual API checks, compileall,
  and diff checks passed.

## v0.8.83 — 2026-08-31

- Validated `ProcessManager.start()` working directories as existing,
  path-like, non-alias directories before spawning child processes.
- Verification: invalid-root regression, manual directory boundary check,
  compileall, and diff checks passed.

## v0.8.82 — 2026-08-31

- Added object-argument validation to `list_processes`, completing consistent
  malformed-input handling across all background process tools.
- Verification: five-tool invalid-input regression, compileall, and diff
  checks passed.

## v0.8.81 — 2026-08-31

- Filtered sensitive environment variables in direct `ProcessManager` child
  processes, closing a credential-leakage bypass outside the tool wrapper.
- Verification: injected-secret manual check, background tests, compileall,
  and diff checks passed.

## v0.8.80 — 2026-08-31

- Enforced destructive-command blocking inside `ProcessManager.start()`, so
  direct manager callers cannot bypass the background tool safety boundary.
- Verification: direct `git clean -xfd` rejection, background tests,
  compileall, and diff checks passed.

## v0.8.79 — 2026-08-31

- Added consistent object-argument validation across all background process
  tools, preventing leaked attribute errors on malformed direct calls.
- Verification: four-tool invalid-input regression, manual API checks,
  compileall, and diff checks passed.

## v0.8.78 — 2026-08-31

- Added explicit object validation to test and diagnostics tool entrypoints,
  replacing leaked `AttributeError` failures with actionable input errors.
- Verification: direct `None`/non-object checks, command tests, compileall,
  and diff checks passed.

## v0.8.77 — 2026-08-31

- Filtered sensitive environment variables from test and diagnostics
  subprocesses, closing a credential-leakage bypass through quality tools.
- Verification: injected-secret manual check, command tests, compileall, and
  diff checks passed.

## v0.8.76 — 2026-08-31

- Applied the shared command risk classifier to test and diagnostics tools,
  blocking destructive commands before approval or execution.
- Verification: hard-block regression, manual destructive-command check,
  targeted command tests, compileall, and diff checks passed.

## v0.8.75 — 2026-08-31

- Tightened explicit command validation for test and diagnostics tools so
  whitespace-only commands cannot fall through to shell execution.
- Verification: direct tool boundary checks, targeted command tests, compileall,
  and diff checks passed.

## v0.8.74 — 2026-08-31

- Added manager-level validation for background commands, rejecting empty,
  non-text, and oversized commands before process creation.
- Verification: direct API boundary checks, background tests, compileall, and
  diff checks passed.

## v0.8.73 — 2026-08-31

- Made `process_status` return a failed tool result for unknown task IDs,
  matching `poll_process` and preventing false-positive status checks.
- Verification: status contract regression, manual unknown-task invocation,
  compileall, and diff checks passed.

## v0.8.72 — 2026-08-31

- Made `process_status` derive its message and metadata from one snapshot,
  preventing contradictory results when a task exits between reads.
- Verification: background tests, manual completion-race inspection,
  compileall, and diff checks passed.

## v0.8.71 — 2026-08-31

- Validated direct background `snapshot()` cursors as non-negative integers,
  preventing negative slicing and leaking internal type errors.
- Verification: cursor boundary regression, background tests, compileall, and
  manual API checks passed.

## v0.8.70 — 2026-08-31

- Hardened background state persistence by rejecting symlink/junction aliases
  before loading or writing task state.
- Verification: alias-path regression, background tests, compileall, and
  manual state-file checks passed.

## v0.8.69 — 2026-08-31

- Hardened background task state persistence with unique temporary files and
  durable flushes before atomic replacement.
- Verification: background tool tests, compileall, diff checks, and manual
  concurrent state-write inspection passed.

## v0.8.68 — 2026-08-31

- Hardened trust persistence by rejecting symlink/junction `.forgecode`
  directories before creating or writing trust records.
- Verification: alias-directory regression, targeted trust tests, and manual
  boundary check passed.

## v0.8.67 — 2026-08-31

- Hardened trust grants with unique, flushed, fsynced temporary files so
  concurrent grants cannot collide or expose a partial record.
- Verification: concurrent grant regression, full pytest, compileall, and
  manual trust-file inspection passed.

## v0.8.66 — 2026-08-31

- Serialized in-process `MemoryStore` read-modify-write mutations so concurrent
  callers cannot silently lose user-managed memory entries.
- Verification: concurrent thread regression, full pytest suite, compileall,
  manual filesystem inspection, and diff checks passed.

## v0.8.65 — 2026-08-31

- Hardened `ToolRegistry.filter()` to preserve validated source snapshots
  without re-reading mutable extension definitions.
- Verification: registry tests, manual post-registration mutation check,
  compileall, and diff checks passed.

## v0.8.64 — 2026-08-31

- Added explicit path-like validation to `WorkspaceGuard`, turning invalid
  string roots into a clear type error instead of an internal attribute error.
- Verification: workspace/tool tests, manual constructor check, compileall,
  and diff checks passed.

## v0.8.63 — 2026-08-31

- Required extension tool definitions to provide a genuine boolean
  `side_effecting` flag, preventing truthy strings from weakening mode policy.
- Verification: registry tests, manual string-flag check, compileall, and diff
  checks passed.

## v0.8.62 — 2026-08-31

- Included `side_effecting` in registered tool definition snapshots so mode
  filtering cannot drift when extensions mutate their definitions later.
- Verification: registry/loop tests, manual side-effect mutation check,
  compileall, and diff checks passed.

## v0.8.61 — 2026-08-31

- Made `ToolRegistry.definitions()` return immutable-definition snapshots,
  preventing callers from mutating registered schemas through introspection.
- Verification: registry/loop tests, manual definition mutation check,
  compileall, and diff checks passed.

## v0.8.60 — 2026-08-31

- Returned deep-copied tool schemas from `ToolRegistry.schemas()`, preventing
  callers from mutating registered provider payloads through the result.
- Verification: registry tests, manual return-value mutation check, compileall,
  and diff checks passed.

## v0.8.59 — 2026-08-31

- Made redaction secret normalization stop after the configured bound,
  preventing infinite generators from being consumed indefinitely.
- Verification: session/redaction tests, manual generator-bound check,
  compileall, and diff checks passed.

## v0.8.58 — 2026-08-31

- Rejected strings and byte strings as direct redaction secret containers,
  preventing accidental character-by-character over-redaction.
- Verification: session/redaction tests, manual helper checks, compileall,
  and diff checks passed.

## v0.8.57 — 2026-08-31

- Isolated registered tool schema names and descriptions from post-registration
  definition mutation, preventing schema lookup failures and payload drift.
- Verification: registry/CLI tests, manual definition mutation check,
  compileall, and diff checks passed.

## v0.8.56 — 2026-08-31

- Snapshot validated tool schemas at registration time so post-registration
  mutation of extension-owned dictionaries cannot alter provider payloads.
- Verification: registry tests, manual schema mutation check, compileall, and
  diff checks passed.

## v0.8.55 — 2026-08-31

- Hardened tool schema registration against recursion-depth failures by
  converting `RecursionError` into a bounded validation error.
- Verification: registry tests, manual 10,000-level schema check, compileall,
  and diff checks passed.

## v0.8.54 — 2026-08-31

- Added strict JSON and 1 MiB size validation for registered tool parameter
  schemas, rejecting non-finite values and oversized provider definitions.
- Verification: registry tests, manual malformed-schema checks, compileall,
  and diff checks passed.

## v0.8.53 — 2026-08-31

- Validated extension tool definitions during registration, bounding names
  and descriptions and requiring object-shaped parameter schemas.
- Verification: registry/CLI contract tests, manual malformed-definition
  check, compileall, and diff checks passed.

## v0.8.52 — 2026-08-31

- Preserved the existing non-finite redaction marker while using a distinct
  marker for finite oversized floats, maintaining compatibility for clients.
- Verification: session/redaction tests, manual serialization checks,
  compileall, and diff checks passed.

## v0.8.51 — 2026-08-31

- Normalized finite but oversized floats in recursive redaction, preventing
  pathological metadata values such as `1e308` from reaching JSON output.
- Verification: session/redaction tests, manual JSON serialization checks,
  compileall, and diff checks passed.

## v0.8.50 — 2026-08-31

- Aligned provider-neutral usage validation with concrete adapters by
  requiring bounded non-empty string field names.
- Verification: provider tests, manual malformed-key validation, compileall,
  and diff checks passed.

## v0.8.49 — 2026-08-31

- Added oversized-integer normalization to recursive metadata redaction,
  preventing extreme provider/tool numbers from reaching logs or JSON output.
- Verification: session/redaction tests, manual 5,000-digit metadata check,
  compileall, and diff checks passed.

## v0.8.48 — 2026-08-31

- Applied bounded secret-list validation directly inside `redact_text()` and
  `redact_value()`, covering callers outside `ToolContext`.
- Verification: session/redaction tests, manual direct-helper checks,
  compileall, and diff checks passed.

## v0.8.47 — 2026-08-31

- Normalized non-finite floats in recursive redaction to a safe placeholder,
  fulfilling the JSON-compatible metadata contract for persisted events.
- Verification: session/redaction tests, manual JSON serialization check,
  compileall, and diff checks passed.

## v0.8.46 — 2026-08-31

- Added bounded secret material validation to `ToolContext`, limiting entries
  to 64 values of at most 4,096 characters before output redaction.
- Verification: tool boundary tests, manual secret-limit checks, compileall,
  and diff checks passed.

## v0.8.45 — 2026-08-31

- Added bounded newline-safe task-ID validation at the `ProcessManager.start()`
  boundary, protecting direct and extension callers from state-key injection.
- Verification: background tests, manual task-ID checks, compileall, and diff
  checks passed.

## v0.8.44 — 2026-08-31

- Extended destructive Git push hard-blocking to short `-f`, `-d`, and
  combined `-fd` options, while preserving ordinary `-u` pushes.
- Verification: command classifier tests, manual short-option variants,
  compileall, and diff checks passed.

## v0.8.43 — 2026-08-31

- Hardened destructive Git push detection to hard-block `--mirror` and
  `--delete` operations alongside force pushes and force refspecs.
- Verification: command classifier tests, manual destructive-push variants,
  compileall, and diff checks passed.

## v0.8.42 — 2026-08-31

- Extended force-push hard-block detection across Git `-c key=value` global
  options, including force refspecs.
- Verification: command classifier tests, manual `git -c` variants,
  compileall, and diff checks passed.

## v0.8.41 — 2026-08-30

- Extended force-push hard-block detection across Git `--git-dir` and
  `--work-tree` global options, including force refspecs.
- Verification: command classifier tests, manual option variants, compileall,
  and diff checks passed.

## v0.8.40 — 2026-08-30

- Hardened the tool result boundary by rejecting non-mapping metadata from
  extensions before redaction and output truncation.
- Verification: tool registry tests, compileall, diff checks, and manual
  malformed-result inspection passed.

## v0.8.39 — 2026-08-30

- Bounded `ToolRegistry` output limits to integer values from 1 through
  1,000,000, rejecting booleans and unbounded memory settings.
- Verification: tool registry tests, compileall, diff checks, and manual
  constructor-boundary validation passed.

## v0.8.38 — 2026-08-30

- Hardened force-push detection to cover `git -C <repo> push` and `+refspec`
  forms, keeping irreversible repository operations at the hard-block level.
- Verification: command-classifier tests, manual variant checks, compileall,
  and diff checks passed.

## v0.8.37 — 2026-08-30

- Added a realistic bounded range for provider usage counters, rejecting
  finite but pathological values such as `1e308` before metrics aggregation.
- Verification: provider tests, compileall, diff checks, and manual extreme
  finite-float validation passed.

## v0.8.36 — 2026-08-30

- Applied the oversized-integer bound to provider usage counters as well as
  tool arguments, preventing custom providers from corrupting metrics with
  extreme numeric values.
- Verification: provider tests, compileall, diff checks, and manual usage
  boundary validation passed.

## v0.8.35 — 2026-08-30

- Hardened oversized-integer validation to use `bit_length()` instead of
  decimal conversion, ensuring extreme provider values are rejected without
  triggering Python's integer string-conversion exception.
- Verification: provider contract tests, compileall, diff checks, and manual
  5,000-digit integer validation passed.

## v0.8.34 — 2026-08-30

- Added a bounded integer magnitude to provider tool arguments, preventing
  oversized numeric values from bypassing JSON budgets and inflating logs or
  downstream validation work.
- Verification: provider contract tests, compileall, diff checks, and manual
  oversized-integer validation passed.

## v0.8.33 — 2026-08-30

- Hardened background-task state recovery with file-size, task-ID, and status
  bounds, and removed untrusted persisted fields from stale-task responses.
- Verification: background tool tests, compileall, diff checks, and manual
  malformed-state inspection passed.

## v0.8.32 — 2026-08-30

- Isolated `EmbeddedSession` stderr readers by reconnect generation so stale
  worker diagnostics cannot contaminate a restarted session.
- Verification: embedding/recovery tests, compileall, and manual reconnect
  diagnostics inspection passed.

## v0.8.31 — 2026-08-30

- Made background task admission atomic and rejected duplicate task IDs,
  preventing concurrent starts from exceeding limits or replacing live tasks.
- Verification: background tool tests, compileall, and manual process-manager
  inspection passed.

## v0.8.30 — 2026-08-30

- Bound `EmbeddedSession` reader threads to their process generation so a
  reconnect cannot leak stale process-exit events into the new session queue.
- Verification: embedding/recovery tests, compileall, and manual reconnect
  queue inspection passed.

## v0.8.29 — 2026-08-30

- Fixed Node `invokeStream()` to close stdin for both RPC and CLI JSONL modes,
  preventing child processes from waiting indefinitely for EOF.
- Verification: Node SDK contract test and manual child-process lifecycle
  inspection passed.

## v0.8.28 — 2026-08-30

- Rejected negative usage counters at the provider-neutral response boundary,
  preventing custom providers from corrupting run metrics and diagnostics.
- Verification: provider contract tests, compileall, and manual response-path
  inspection passed.

## v0.8.27 — 2026-08-30

- Rejected non-finite timeout values in Python embedding `session_wait` and
  `session_events`, aligning SDK validation with Node and RPC behavior.
- Verification: Python embed contract tests and compileall passed.

## v0.8.26 — 2026-08-30

- Added the missing Python `session_status()` embedding helper and package-level
  export, aligning Python SDK coverage with the Node SDK and RPC contract.
- Verification: Python embed contract tests, compileall, and diff checks passed.

## v0.8.25 — 2026-08-30

- Added bounded finite timeout validation to the Node `sessionWait` helper,
  matching the Python SDK and RPC contract.
- Verification: Node SDK contract, Python embed contract, and diff checks passed.

## v0.8.24 — 2026-08-30

- Exposed bounded `wait` and event `type` filtering in Python and Node
  `session.events` SDK helpers, matching the underlying RPC contract.
- Verification: Python/Node SDK contract tests, compileall, and diff checks passed.

## v0.8.23 — 2026-08-30

- Refreshed top-level lifecycle metadata in `session.events` after long-poll
  updates so state, sequence, execution, and active flags match returned events.
- Verification: targeted RPC regression, manual durable update simulation,
  compileall, and diff checks passed.

## v0.8.22 — 2026-08-30

- Refreshed `execution` metadata in `session.wait` responses after durable
  cross-process updates, keeping lifecycle fields internally consistent.
- Verification: targeted RPC regression, manual durable update simulation,
  compileall, and diff checks passed.

## v0.8.21 — 2026-08-30

- Hardened durable RPC refresh when a process changes state without advancing
  its event sequence, while preserving active in-process worker ownership.
- Verification: targeted lifecycle regressions, manual durable-state injection,
  compileall, and diff checks passed.

## v0.8.20 — 2026-08-30

- Made long-polling RPC `session.wait` and `session.events` observe durable
  cross-process updates with bounded polling while retaining condition-based
  in-process wakeups.
- Verification: targeted lifecycle regressions, manual second-writer timing
  simulation, compileall, and diff checks passed.

## v0.8.19 — 2026-08-30

- Added safe cross-process refresh for read-only RPC session views, allowing
  status, result, wait, and event polling to observe newer durable cursors.
- Verification: targeted regression, manual second-writer simulation,
  compileall, and diff checks passed.

## v0.8.18 — 2026-08-30

- Corrected `session.wait` responses so lifecycle `active_flags` are refreshed
  after a run transitions to a terminal state.
- Verification: targeted RPC regression, manual lifecycle simulation,
  compileall, and diff checks passed.

## v0.8.17 — 2026-08-30

- Hardened RPC session recovery by restoring execution metadata and filtering
  malformed or duplicate persisted event cursors before exposing them to clients.
- Verification: targeted RPC lifecycle tests, compileall, diff checks, and a
  manual restart simulation with malformed events passed.

## v0.8.16 — 2026-08-30

- Added a `cache_hit` diagnostic to `/status` metrics so operators can tell
  whether the session aggregate was reused or recomputed.
- Verification: manually reviewed cache hit/miss paths and confirmed the field
  is additive metadata; status CLI tests and compile checks passed.

## v0.8.15 — 2026-08-30

- Strengthened `/status` cache invalidation with filesystem ctime/inode
  identity in addition to sequence, size, and mtime.
- Verification: cache-key replacement scenarios were manually reviewed;
  status CLI tests and compile checks passed.

## v0.8.14 — 2026-08-30

- Extended `/status` metric cache invalidation with session file size and
  mtime, so appends from another process become visible without stale reuse.
- Verification: cache-key logic was manually reviewed for append, missing-file,
  and stat-error paths; existing status tests passed.

## v0.8.13 — 2026-08-30

- Cached interactive `/status` aggregate metrics by the session event cursor,
  avoiding repeated full JSONL scans when no new events exist.
- Verification: CLI status tests passed; manual review confirmed cache
  invalidation on append and unchanged malformed-stream diagnostics.

## v0.8.12 — 2026-08-30

- Corrected live status semantics so recovery-required runs are reported as
  stopped rather than active.
- Verification: recovery-state regression passed; manual review checked all
  lifecycle states and confirmed only executing phases report active.

## v0.8.11 — 2026-08-30

- Added hard step and tool-call limits to live run diagnostics so operators can
  distinguish normal progress from budget exhaustion.
- Verification: status snapshot regression passed; manual review confirmed the
  values are configuration metadata only and cannot alter enforcement.

## v0.8.10 — 2026-08-30

- Added the current durable event sequence to live run snapshots, allowing
  clients to correlate status refreshes with incremental session reads.
- Verification: snapshot regression passed; manual review confirmed the value
  is sourced only from the validated session append cursor and is zero without
  a session.

## v0.8.9 — 2026-08-30

- Reset live status counters at run entry so reusable embedded loop instances
  cannot report stale provider/tool activity from a prior invocation.
- Verification: status regression passed; manual review confirmed the reset
  occurs only after prompt validation and before repository/provider work.

## v0.8.8 — 2026-08-30

- Added live provider-request and tool-call counters to the bounded run status
  snapshot for diagnosing retries and excessive tool activity.
- Verification: status snapshot assertions passed; manual review confirmed
  counters include attempted provider requests and only dispatched tool calls.

## v0.8.7 — 2026-08-30

- Included RunService loop diagnostics in interactive `/status` output while
  preserving the existing controller metrics and additive machine contract.
- Verification: CLI status/inspect contract tests passed; manual review checked
  lock ordering and idle fallback behavior.

## v0.8.6 — 2026-08-30

- Exposed the bounded live run snapshot through `RunService`, including
  startup and pending-control state for CLI/RPC clients.
- Verification: service status unit test and manual startup/idle race review.

## v0.8.5 — 2026-08-30

- Added a bounded AgentLoop status snapshot with lifecycle, step, timing,
  steering, cancellation, and audit fields for live diagnostics.
- Verification: targeted `tests/test_loop.py -k status_snapshot` passed;
  manual review confirmed no prompts, tool arguments, paths, or secrets are
  exposed by the snapshot.

## v0.8.4 — 2026-08-30

- Added interactive `/memory` management so users can inspect, add, remove, or clear workspace memory without leaving `fcc` or involving the model.
- Multi-word additions are preserved and invalid actions fail with bounded usage guidance.
- Manually verified interactive dispatch and reran focused controls/memory tests.

## v0.8.3 — 2026-08-30

- Added bounded workspace-local user memory with explicit `memory add/show/remove/clear` commands.
- Memory is atomically persisted, schema-checked, isolated under `.forgecode`, injected as untrusted context, and never exposed as a model mutation tool.
- Manually verified round-trip, tamper rejection, context loading, and removal.

## v0.8.2 — 2026-08-30

- Added bounded `/steer <message>` control for guiding an active run at its
  next safe model boundary without interrupting tool side effects.
- Steering messages are redacted, auditable, capped, and cleared on
  cancellation; existing follow-up queue behavior remains unchanged.
- Manually verified a two-turn steering run and reran the focused regression
  suite.

## v0.8.1 — 2026-08-30

- Version synchronization release for the assessment submission build.
- Verified the interactive CLI, machine-readable output modes, and local
  provider-independent startup path.

## v0.7.61 — 2026-08-30

- Google `generateContent` requests now preserve the selected model field when
  translating from the provider-neutral request shape.

## v0.7.60 — 2026-08-30

- Trajectory evaluation now distinguishes newly opened sessions (`not_started`)
  and active sessions (`in_progress`) from genuine failures, avoiding a false
  `failed` status when no run has completed yet.

## v0.7.59 — 2026-08-30

- Added bounded validation for provider capability declarations, rejecting
  invalid limits, malformed transport names, and duplicate transports before
  they reach AgentLoop or RPC diagnostics.

## v0.7.58 — 2026-08-30

- Ollama responses now normalize local-model `message.tool_calls` into the
  provider-neutral tool-call contract for both JSON and streaming transports.
- Tool IDs, names, arguments, and `tool_calls` finish reasons are preserved.

## v0.7.57 — 2026-08-30

- Provider adapters now translate prior assistant tool calls and tool results
  into Anthropic content blocks and Google `functionCall`/`functionResponse`
  parts, preserving multi-turn tool conversations.

## v0.7.56 — 2026-08-30

- Google streaming responses now normalize Gemini `functionCall` parts into
  ForgeCode tool-call fragments and select `tool_calls` when a function call
  accompanies the provider's `STOP` finish reason.

## v0.7.55 — 2026-08-30

- Google provider requests now translate OpenAI-style tool schemas into
  `functionDeclarations` for `generateContent`.
- Google `functionCall` response parts are normalized into ForgeCode tool calls,
  preserving arguments and finish reasons.

## v0.7.54 — 2026-08-30

- Anthropic streaming responses now normalize `tool_use` blocks and
  incremental `input_json_delta` arguments into the provider-neutral tool-call
  protocol, preserving tool IDs, names, arguments, and finish reasons.
- Existing text-only streaming behavior remains compatible.

## v0.7.53 — 2026-08-30

- Fixed named provider adapters so their default production transport also
  applies Anthropic, Google, and Ollama wire-format translation.
- Custom transports remain supported and continue to receive the same
  provider-specific normalization.

## v0.7.52 — 2026-08-30

- `rpc.describe` now advertises the complete bounded session event catalogue,
  including model, tool, verification, context, transaction, and recovery
  events already returned by `session.events`.
- The catalogue is observational metadata only and does not grant permissions.

## v0.7.51 — 2026-08-30

- Model progress, request, and response events now share a stable bounded
  `turn_id`, allowing RPC/session clients to correlate each model turn without
  depending on provider-specific request IDs.
- Existing event fields and provider request identities remain unchanged.

## v0.7.50 — 2026-08-30

- RPC session status now includes bounded `active_flags` (`turn_in_progress`,
  `paused`, or `recovery_required`) while preserving the existing state field.
- Flags are descriptive metadata only and do not grant control or execution.

## v0.7.49 — 2026-08-30

- Provider capabilities now advertise transport modes (`json`, and `sse` when
  streaming is enabled), making protocol negotiation explicit without claiming
  unsupported WebSocket support.

## v0.7.48 — 2026-08-30

- Added `/queue`, a read-only interactive view of pending follow-up capacity and
  worker activity; queued message contents are intentionally not exposed.

## v0.7.47 — 2026-08-30

- Approval audit events now carry a stable risk `scope`, normalized `decision`,
  and the policy decision source alongside the existing bounded arguments.
- This is additive observability only; approval, plan, trust, and WorkspaceGuard
  enforcement remain unchanged.

## v0.7.46 — 2026-08-30

- `session.events` now reports `has_more`, allowing clients to distinguish an
  exhausted cursor from a bounded page that needs another request.

## v0.7.45 — 2026-08-30

- RPC session event responses now include stable `event_id`, `session`, and
  `schema_version` metadata while preserving existing event payloads and cursors.
- Event identity is derived from the session handle and monotonic sequence, making
  client polling and deduplication deterministic.

## v0.7.44 — 2026-08-30

- `rpc.describe` now publishes a versioned session event schema and the stable
  event type catalog used by `session.events` polling.
- The catalog is explicitly forward-compatible with unknown future event types.

## v0.7.43 — 2026-08-30

- `session.events` supports bounded long-polling with `wait` (0–30 seconds),
  waking when a new event is persisted while preserving cursor semantics.
- Completion and failure paths now notify event waiters without changing worker
  isolation or side-effect approval behavior.

## v0.7.42 — 2026-08-30

- `session.events` now accepts an optional bounded `type` filter, making RPC
  event polling cursor-friendly without streaming unbounded data.
- Responses echo the filter and retain existing `after`, `limit`, and truncation
  metadata for deterministic clients.

## v0.7.41 — 2026-08-30

- `rpc.describe` now publishes an explicit approval capability catalog: supported
  modes, granular risk scopes, and unsupported Codex-style domains are separated.
- Clients can negotiate safety behavior without mistaking capability discovery for
  authorization; existing workspace and approval checks remain authoritative.

## v0.7.40 — 2026-08-30

- RPC session recovery now validates persisted lifecycle states; unknown future
  values are restored as `recovery_required` instead of ambiguous active states.
- Added regression coverage for forward-incompatible state records.

## v0.7.39 — 2026-08-30

- Serialized AgentLoop pause/resume flag access across the worker thread and
  event loop, reducing approval-boundary races that could incorrectly cancel
  an interactive task.
- The pause behavior remains cooperative and fail-closed; no side effect runs
  while an interactive pause is pending.

## v0.7.38 — 2026-08-30

- RPC session recovery now validates that persisted `session_path` is a
  workspace-local relative `.jsonl` path under `.forgecode/sessions`.
- External, traversal, malformed, or otherwise unsafe paths are ignored before
  a session can be restored.

## v0.7.37 — 2026-08-30

- Hardened RPC session recovery with a 512 KiB record-size limit, alias
  rejection, and minimum workspace/mode/session schema validation.
- Oversized, malformed, or symlinked records are ignored rather than loaded
  into the daemon session table.

## v0.7.36 — 2026-08-30

- Added read-only `git_worktree_reconcile` to compare actual Git worktrees with
  ForgeCode ownership records and report healthy, unmanaged, missing-path, or
  mismatched entries without mutating either source.
- Registered the tool in the read-only policy, RPC capability catalog, and
  AgentLoop parallel allowlist.

## v0.7.35 — 2026-08-30

- `rpc.describe` now exposes a bounded built-in tool capability catalog with
  risk groups and side-effect markers, while explicitly stating that active
  policies may narrow the catalog and that discovery is not authorization.
- Added machine-contract coverage for tool capability discovery.

## v0.7.34 — 2026-08-30

- Added a 256 KiB bound and strict key/value validation when reading managed
  worktree ownership metadata.
- Oversized or malformed state now fails closed with a structured tool error
  before JSON parsing can consume unbounded input.

## v0.7.33 — 2026-08-30

- Worktree listing and removal now convert invalid or aliased ownership
  metadata into bounded structured tool errors instead of leaking exceptions
  into the agent loop.
- Added regression coverage for the fail-closed metadata alias boundary.

## v0.7.32 — 2026-08-30

- Hardened managed worktree ownership persistence with atomic replacement and
  process-local serialization, preventing partial JSON state during concurrent
  or interrupted updates.

## v0.7.31 — 2026-08-30

- Worktree ownership metadata is now updated with a bounded, same-directory
  temporary file, `fsync`, and atomic replace under an in-process lock.
- Interrupted or concurrent updates cannot expose a partially written JSON
  state file; temporary artifacts are cleaned up on failure.

## v0.7.30 — 2026-08-30

- Worktree creation now records bounded, non-sensitive session ownership
  metadata in `.forgecode/worktrees.json`.
- Worktree listing surfaces managed names and run IDs, while removal rejects a
  mismatched session owner and cleans the record after successful removal.

## v0.7.29 — 2026-08-30

- Added approved `git_worktree_create` and `git_worktree_remove` tools for
  isolated, workspace-local lifecycles under `.forgecode/worktrees`.
- Creation and removal remain unavailable in plan mode, are classified under
  the changes risk group, and reject unsafe names before Git runs.

## v0.7.28 — 2026-08-30

- Added `lsp_status`, a bounded read-only capability discovery tool that reports
  common language-server executables on `PATH` without starting processes or
  claiming full LSP support.
- Registered the tool in the default registry and read-only execution policy,
  with regression coverage for its discovery-only contract.

## v0.7.27 — 2026-08-30

- Extended bounded read-only batch parallelism to `git_worktrees` and
  `symbol_hover`, preserving serial execution for mixed or side-effecting calls.
- Added regression coverage for the new read-only batch members.

## v0.7.26 — 2026-08-30

- Fixed `rpc.describe` request-id handling so capability discovery follows the
  same bounded replay and idempotency contract as other JSONL RPC methods.
- Added regression coverage for repeated capability requests.

## v0.7.25 — 2026-08-30

- Static `symbol_hover` now recognizes common JavaScript/TypeScript arrow
  function and exported variable definitions.
- Results remain bounded and explicitly marked as static precision.

## v0.7.24 — 2026-08-30

- Exposed `rpc_describe()` through the Python embedding API and package-level
  exports, matching the JSONL `rpc.describe` capability discovery method.
- Added embedding contract coverage without changing session execution.

## v0.7.23 — 2026-08-30

- Added read-only `rpc.describe` capability discovery with protocol version,
  session controls, and explicit safety guarantees.
- Existing JSONL RPC methods and request/replay semantics remain unchanged.

## v0.7.22 — 2026-08-30

- Human `/tools` output is now grouped by risk category and marks
  side-effecting tools, making the permission boundary visible at a glance.
- Machine-readable tool output remains unchanged.

## v0.7.21 — 2026-08-30

- Added `symbol_hover`, a bounded static symbol definition/context tool that
  safely degrades when no definition is found and explicitly reports
  `precision = "static"`.
- Included the tool in read-only policy and discovery surfaces.

## v0.7.20 — 2026-08-30

- Added the read-only `git_worktrees` tool to inspect bounded worktree paths,
  branches, and HEADs without creating, switching, or mutating worktrees.
- The listing is workspace-validated and capped at 64 entries.
- Included `git_worktrees` in the `read_only` risk group and configuration
  validation so policy filtering and tool discovery stay consistent.

## v0.7.19 — 2026-08-30

- Approval audit events now identify whether a scoped allow/deny or the global
  fallback policy made the decision, improving explainability without logging
  commands, file contents, or credentials.

## v0.7.18 — 2026-08-30

- Added optional `[approval_scopes]` configuration for per-domain `allow`,
  `ask`, or `deny` decisions across changes, execution, and evidence tools.
- Existing global approval modes remain compatible; scoped decisions are
  exposed in policy diagnostics without exposing credentials.

## v0.7.17 — 2026-08-30

- Background task metadata is persisted under `.forgecode` without commands or
  output; tasks observed after a process restart are reported as `stale` and
  explicitly non-recoverable instead of being replayed.
- Added bounded persistence and restart-safety regression coverage.

## v0.7.16 — 2026-08-30

- CLI tool policy now accepts audited risk groups (`read_only`, `changes`,
  `execution`, and `evidence`) and expands them to the available exact tools.
- Group expansion preserves existing unknown-tool, duplicate, overlap, and
  registry-narrowing checks; configuration files continue to use exact names.
- Added parser regression coverage for allow and deny group usage.

## v0.7.15 — 2026-08-30

- Read-only batch scheduling now stays serial whenever lifecycle hooks are
  configured, preserving hook ordering and avoiding concurrent hook state.
- Added regression coverage for the hook-enabled safety fallback.

## v0.7.14 — 2026-08-30

- Optional streaming now falls back to JSON when a gateway returns HTTP 404,
  405, or 501 for the SSE endpoint; required streaming remains fail-closed.
- Added regression coverage for HTTP capability fallback.

## v0.7.13 — 2026-08-30

- AgentLoop now fails fast when a provider is configured to require streaming
  but explicitly reports streaming unsupported, with a bounded capability
  mismatch result and audit event.
- Added regression coverage for required-stream capability negotiation.

## v0.7.12 — 2026-08-30

- Tool inventory output now shows an explicit total count in human and machine
  responses, making capability discovery consistent with the registered set.

## v0.7.11 — 2026-08-30

- `kill_process` now distinguishes already-exited, confirmed termination, and
  unresolved termination after a bounded wait instead of reporting cancellation
  optimistically.
- Added regression coverage for confirmed termination metadata.

## v0.7.10 — 2026-08-30

- Removed the full command from successful `run_background` tool metadata;
  task IDs and status remain visible while command arguments stay out of model
  context and structured tool results.
- Added regression coverage for startup metadata non-disclosure.

## v0.7.9 — 2026-08-30

- Removed command text from `list_processes` summaries so task discovery cannot
  expose credentials or other sensitive command arguments.
- Added regression coverage for command-argument non-disclosure.

## v0.7.8 — 2026-08-30

- Bounded background-process history to prevent completed task metadata from
  growing without limit during long-lived sessions; active tasks are never
  evicted.
- Added regression coverage for history eviction and active-task protection.

## v0.7.7 — 2026-08-30

- Synchronized the interactive and machine tool inventories after adding
  `list_processes`; it is exposed as a read-only capability in `/tools`.

## v0.7.6 — 2026-08-30

- Added a bounded `list_processes` background-task tool for discovering active
  and completed tasks without replaying captured output.

## v0.7.5 — 2026-08-30

- Hardened background task observability with a 64-task active limit, strict
  output accounting, bounded line truncation, process IDs, and stable elapsed
  duration after completion.
- Added regression coverage for hard output bounds and stable completion state.

## v0.7.4 — 2026-08-30

- Provider capability declarations are now enforced before a tool-enabled
  request: an explicit `tool_calling=false` provider fails fast with a bounded
  capability-mismatch result instead of sending an incompatible request.
- Model request audit events now include the provider capability snapshot.
- Added regression coverage for fail-fast capability negotiation.

## v0.7.3 — 2026-08-30

- Added guarded, bounded `find_definition` and `find_references` tools for
  language-neutral static navigation across common source files.
- Navigation never imports or executes project code and returns structured,
  capped matches suitable for model context and audit output.
- Added regression coverage for definition/reference results and workspace
  boundaries.

## v0.7.2 — 2026-08-30

- Restricted read-only parallel scheduling to an explicit audited allowlist.
- Bounded in-flight work now honors cancellation for queued calls and returns
  a paired `cancelled_before_start` result for every interrupted tool call.
- Added regression coverage for cancellation and protocol-safe tool pairing.

## v0.7.1 — 2026-08-30

- Same-turn batches made entirely of read-only tools now run with bounded
  concurrency (up to four workers), while mixed or side-effecting batches stay
  serial. Results, call IDs, checkpoints, and audit events retain model order.
- Added regression coverage for concurrent execution and deterministic result
  ordering.

## v0.7.0 — 2026-08-29

- Full regression gate now passes: 485 tests passed, with 8 Windows
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
- Added read-only interactive `/diff`, reusing the bounded Git diff path so
  users can inspect changes without asking the model or invoking side effects.
- Slash-command completion now includes `/diff`, and unknown commands provide
  a close-match suggestion when a typo is detected.
- Completion now also suggests valid arguments for `/mode`, `/plan`, `/model`,
  and `/undo`, reducing command syntax friction during live use.
- Optional streaming requests now retry truncated or malformed SSE frames
  before surfacing an error; incomplete tool calls stay inside the provider and
  cannot reach the executor as partial side effects.
- `/status` now retains the last completed run's duration and tool-step count,
  so long-task evidence remains visible after the worker returns to idle.
- `/status` now also reports cumulative provider attempts, retries, tool calls,
  and context characters for the current session.
- Added read-only interactive `/context`, which reports bounded context-index
  health, stale entries, exclusions, and diagnostics without exposing source
  content or rebuilding the index implicitly.
- Context health responses now omit full index entries and return only bounded
  metadata, keeping interactive and machine envelopes predictable on large
  repositories.
- Context health now reports bounded symbol totals and language distribution,
  making index quality visible without exposing source content.
- Added read-only interactive `/events` to show the last 40 persisted event
  types and outcomes, making long-run progress and failures inspectable without
  exposing event payload contents.
- Event timeline rows now include bounded relative elapsed time, making provider
  waits, retries, and tool activity visible at a glance.
- `/events` accepts an optional limit from 1 to 100, allowing focused inspection
  of the latest session activity without changing the persisted audit log.
- Event failures now show their bounded error code inline in the human timeline,
  so common recovery causes are visible without opening the raw session log.
- `/events` accepts an optional event-kind filter (for example
  `/events 20 error`), while retaining strict bounds on query size.
- `/events` argument completion now suggests common audit kinds and the human
  renderer displays the active filter explicitly.
- `/events <kind>` is now a shorthand for filtering the latest 40 events,
  matching the completion menu and keeping the full bounded form available.
- Event query callbacks retain compatibility with older embedded integrations:
  zero-argument callbacks continue to work while the built-in session handler
  receives bounded limit and kind parameters.
- Event timeline rows now include validated per-event duration when available,
  alongside relative position in the run.
- Empty `/events` filters now explicitly report `No matching events`, separating
  a healthy no-match query from an unavailable or unreadable audit stream.
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
# v0.8.0 — 2026-08-30

- Refined the interactive terminal presentation with persistent command hints,
  visible tool cards, clearer error guidance, and phase-aware progress output.
- Long-running model waits are explicitly distinguished from active tool work.
- Verification: targeted interactive UI tests, compileall, and forgecode doctor.
