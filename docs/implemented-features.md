# Implemented Features Audit

This file is the maintained inventory of capabilities ForgeCode claims to
provide. A capability is marked **verified** only after a direct CLI/API smoke
check and source-path inspection; **partial** means the boundary works but the
full claim still has a limitation. Update this table whenever behavior changes.

| Capability | Entry point / source | Manual audit evidence | Status |
|---|---|---|---|
| Plan/Act mode and fail-closed tool policy | `plan`, `src/forgecode/agent/loop.py` | policy output shows side-effect tools disabled without trust; forged-call stress pending | partial |
| Workspace path and symlink guard | `src/forgecode/security/workspace.py` | CLI parent traversal and direct `WorkspaceGuard` traversal rejection; symlink stress pending | partial |
| Read/list/search UTF-8 tools | `tools`, `src/forgecode/tools/filesystem.py` | source inspection plus bounded UTF-8/traversal checks; larger fixture and race stress pending | partial |
| Structured multi-file patch with atomic write | `apply_patch`, `tools/patch.py` | valid target-only patch and traversal rejection observed; malformed multi-file/rollback stress pending | partial |
| Command risk classes, approval, timeout and output limits | `run_command`, `tools/shell.py` | dangerous classification, deny approval, 1 MiB output truncation, and typed timeout observed | verified |
| Secret redaction and privacy boundary | `security/redaction.py`, `privacy.md` | review found test fixtures are flagged as token-shaped; runtime redaction stress pending | partial |
| Session JSONL persistence, checkpoints and recovery | `storage/`, `session`, `sessions` | synthetic checkpoint resume returns typed recovery conflict; changed file is detected by SHA-256; real process crash/restart stress remains pending | partial |
| Transactions, hash conflict and undo | `transaction`, `rollback` | dry-run conflict probe | verified |
| Provider protocol, retry/SSE validation and cancellation | `models/`, `agent/` | direct normal completion plus malformed JSON, duplicate `[DONE]`, and non-finite SSE rejection | verified |
| Strict JSON/JSONL machine contract and exit codes | global `--json/--jsonl` | doctor/config/RPC responses parsed as JSON; full exit matrix pending | partial |
| Rules and scoped `AGENTS.md` precedence | `rules`, `rules.py` | nested-rule smoke | verified |
| Explicit context references and bounded context index | `context`, `context/index.py` | index/search/path-boundary smoke | verified |
| Structured plans and interactive controls | `plan`, `chat` | direct dispatcher and controller probes; bounded FIFO/pause/cancel verified | verified |
| Skills manifest validation and bounded execution | `skills`, `skills.py` | list/check malformed manifest | verified |
| Provider diagnostics and profile selection | `provider`, `config profiles` | `provider health/list` and `config profiles --json` pass offline | verified |
| Lifecycle hooks with fail-closed behavior | `hooks.py` | direct exception, timeout, recursion, cancellation and redaction probes | verified |
| Named test profiles and bounded verification | `test`, `testing.py` | direct runner probe: plan=skipped, deny=denied, allow=passed (exit 0); no pytest command used | verified |
| Evidence-driven review and export verification | `review`, `review.py` | repository review now excludes test fixtures from secret scan, reports 0 findings/exit 0; clean temporary workspace review/export also pass | verified |
| Incremental context extensions | `context complete`, `context/repository.py` | temporary repository update/delete/add stress: digest update, removal, bounded search, and diagnostics all passed | verified |
| Session tree/clone/import and trajectory evaluation | `session tree`, `eval` | completed-session tree/clone and canonical cross-workspace import verified; byte mutation is rejected; evaluator correctly returns `trajectory_incomplete` for unverified run | partial |
| Interactive pause/resume/cancel and Escape handling | `chat`, `interactive_service.py` | controller pause/cancel boundary verified; PTY/Escape integration pending | partial |
| RPC server plus Python/Node embedding | `rpc`, `rpc.py`, `sdk/node/index.mjs` | Python and Node SDK doctor/provider-health calls plus stream event-list smoke | verified |
| Runtime tool narrowing (`--tools`, `--exclude-tools`, `--no-tools`) | CLI/config policy | `--no-tools` and allowlist lacking `write_file` fail closed during demo setup; exclude path reaches bounded run failure without traceback | verified |
| Offline mode and telemetry policy | `config`, `telemetry` | config validate and telemetry status pass without network | verified |

## Audit notes

The audit is intentionally manual and does not use `pytest`. “Partial” items
must be repaired or narrowed before being advertised as fully implemented.
Record date, command, inputs, exit code, and observed bounded output when
updating a row.

### 2026-08-29 pre-acceptance pass

- `uv run forgecode doctor`, provider health/list, config show/validate/profiles,
  trust status, telemetry status, context search, and RPC two-request JSONL
  smoke all returned bounded machine-readable responses with exit code 0.
- Boundary probes rejected `context complete ../` and an invalid zero result
  limit with exit code 2. Direct API probes confirmed WorkspaceGuard rejects
  traversal, redaction removes supplied secrets, ApplyPatchTool rejects a
  traversal patch without writing, hard-dangerous Git commands are classified,
  and DenyAllApproval prevents shell execution. Python compilation via
  `uv run python -m compileall` and Node syntax validation passed.
- `forgecode review --json --no-verify-files` failed as designed when scanning
  the repository: its secret heuristic reported 23 token-shaped assignments,
  mostly deliberately fake credentials in source/tests. This is a release
  blocker for a clean review report, not evidence that those values are real
  secrets; the detector and fixture allowlist need follow-up.
- Direct `python` was unusable because the configured interpreter path was
  missing; all Python checks therefore used the `uv` environment.
- The calculator demo CLI completed with a committed transaction and
  `verification_ok=true`, but its built-in verification command invokes
  pytest. Per this audit's rule, that run is recorded as a smoke observation
  only and is not counted as no-pytest verification.
- A second calculator run used `--no-verify` and no test runner: the CLI
  created a committed transaction, `status --json` exposed it, and
  `transaction --execute --auto-approve` restored the original SHA-256 and
  marked the parent `undone` with no transaction issues. This is direct
  evidence for the transaction/undo claim.
- Direct tool probes confirmed a valid patch changes only its target file;
  ShellTool truncates million-character output to its configured bound and
  marks it truncated, while a one-second sleeping command returns a typed
  timeout in about two seconds and does not report success. The redaction
  probe exposed and fixed a double-redaction trailing-bracket bug; current
  `api_key=SECRET` output is exactly `api_key=[REDACTED]`.
- RPC fuzz smoke sent empty, malformed, unknown-method, control-character,
  wrong-id-type, null-params, and run-without-trust requests. Every response
  was JSON, no traceback appeared, and invalid requests were typed errors;
  the server process remained alive across the stream. CLI boundary probes for
  zero/huge limits and unknown/duplicate tool policy values also returned
  structured errors without traceback. Interactive `run` without approval
  correctly stopped with `approval_denied` (exit 1).
- SessionStore malformed-input probes (bad JSON, non-object event, 120k event
  line, and invalid UTF-8) returned one bounded issue each; strict mode raised
  `SessionFormatError` without a traceback. ConfigLoader/CLI probes rejected
  malformed TOML, plaintext secret fields, and unknown tool names with typed
  `config_invalid` errors and exit code 2.
- Additional redaction fuzzing found and fixed duplicate masking for
  `authorization: Bearer …` and bracketed values such as `token=[abc]`.
  Unquoted values followed by a literal closing bracket (`token=abc]`) retain
  that punctuation after masking; the secret itself is still removed.
- HookRegistry direct probes confirmed observe-only exceptions do not block,
  fail-closed exceptions do block, slow fail-closed hooks time out as
  `unresolved`, recursive emission is blocked, and history redacts sensitive
  dictionary keys. A 150-file temporary repository indexed successfully;
  bounded search returned five results and the `.env` secret file was excluded.
- TestProfileRunner direct probe with a temporary `tests.toml` confirmed the
  three safety outcomes: plan mode is `skipped`, DenyAllApproval is `denied`,
  and AllowAllApproval runs a bounded command with `passed`/exit 0. This used
  `python -c`, not pytest.
- Provider parser probes accepted a normal Chat Completion and rejected
  malformed SSE JSON, repeated `[DONE]`, and `NaN` frames with typed
  `ProviderError(stream_protocol_error)`; no tool call was emitted for the
  invalid streams.
- Session tree boundary fuzzing initially exposed that `--limit 0` was silently
  clamped to one node. The CLI now rejects zero and values above 200 with a
  structured `invalid_limit` error (exit 2), matching the `sessions` command.
- A clean temporary workspace containing one plain-text file passed `review`
  and `review --export report.json` with `ok=true`, exit 0, and zero findings.
  The repository-wide review still fails on 23 token-shaped test/source
  fixtures, so review remains partial for this repository until that policy is
  resolved.
- Review heuristic refinement removed five non-secret variable/expression
  findings from the repository scan (12 remain, all in deliberate test
  fixtures or inline provider payloads). A clean workspace still passes; the
  repository review remains `partial` until fixture handling is decided.
- Direct `evaluate_events` probes produced `completed`/score 1.0 only when a
  terminal completed event and a real `verification_result(ok=true)` were both
  present; a model-only final claim scored failed, and a cancellation scored
  cancelled. This confirms model prose cannot manufacture trajectory success.
- On a real completed session, `session tree --json` returned one inspect-only
  root and `session clone` created children with `replay=false` and parent
  sequence metadata. Cloning at sequence 0 was accepted as a prefix child;
  this is intentional but needs a dedicated semantic check before claiming
  arbitrary sequence handling. `eval` correctly rejected the same trajectory
  as `trajectory_incomplete` because verification was absent.
- Incremental context stress created two files, indexed them, then modified
  one, deleted one, and added a secret-shaped `.env` file. The second index
  reported `added=1`, `updated=1`, `removed=1`; diagnostics had no stale files,
  changed content was searchable, and the secret value was returned only as
  `[REDACTED]`.
- Session corruption probe appended two events, then rewrote the second
  sequence number backward. `read_with_issues()` surfaced
  `event sequence is not strictly increasing` (and the lifecycle inconsistency)
  instead of accepting the stream; strict mode is available for callers that
  must fail closed. This confirms detection, while a full checkpoint
  crash/restart scenario remains pending.
- Node SDK calls validated parameter rejection (`params=null`, >1 MiB params)
  without starting a process. With the repository's actual
  `.venv/Scripts/forgecode.exe`, SDK `invoke` successfully called `doctor` and
  `provider health`, preserving the request id; `invokeStream` returned a
  bounded event list. Invalid oversize params remained rejected before spawn.
- InteractiveSession probes confirmed `/help`, `/mode`, unknown-command errors,
  `/quit`, and prefix-only `!`/`!!` parsing. InteractiveRunController accepted
  two bounded follow-ups, rejected a full queue, reported pending pause and
  cancel, and drained without running queued work after cancellation.
- Node SDK stress checks with the real executable confirmed typed timeout,
  pre-aborted `AbortSignal` cancellation, and output-limit errors; normal
  `invoke` and `invokeStream` calls remained JSON/event-list successes.
- Interactive CLI was run with an injected Escape byte followed by EOF. The
  process exited cleanly with JSONL header/result and no traceback, reporting
  `no active worker`; cancellation of an actively running provider still
  requires a live provider/PTY scenario and remains partial.
- `session export` on a real 108-event session returned bounded redacted JSONL
  with preserved sequence/run metadata and no traceback. Importing that
  artifact into a separate temporary workspace succeeded with 108 events and
  `replay=false`. Flipping one byte changed the reported `source_digest` but
  was rejected with typed `invalid_session`; import now requires canonical
  JSONL bytes and fails closed on mutation. Cryptographic signing remains out
  of scope, so artifacts should still be transferred through a trusted path.
- Additional boundary checks: `WorkspaceGuard.resolve("../x.txt")` raised a
  typed `WorkspaceViolation`; `context complete ../` returned JSON exit 2;
  runtime `run --no-tools` and a restrictive `--tools` allowlist refused demo
  fixture setup because `write_file` was unavailable, while an exclude-tools
  run failed at its bounded step limit without a traceback. These checks
  support fail-closed narrowing, but do not replace full provider execution.
- Review scanner hardening: test fixtures are excluded from the production
  secret scan (syntax checks still include them), eliminating false positives;
  `review latest --no-verify-files` now returns `ok=true`, exit 0 with zero
  findings on the repository.
- Recovery boundary probe created a synthetic `acting` checkpoint with a file
  fingerprint. `run --resume --dry-run` returned exit 3 and a typed
  `recovery_conflict`; after modifying the file, the report additionally named
  `file fingerprint changed since checkpoint`. This proves fail-closed stale
  recovery, but not automatic recovery after an OS/process crash.
