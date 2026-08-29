# Implemented Features Audit

This file is the maintained inventory of capabilities ForgeCode claims to
provide. A capability is marked **verified** only after a direct CLI/API smoke
check and source-path inspection; **partial** means the boundary works but the
full claim still has a limitation. Update this table whenever behavior changes.

| Capability | Entry point / source | Manual audit evidence | Status |
|---|---|---|---|
| Plan/Act mode and fail-closed tool policy | `plan`, `src/forgecode/agent/loop.py` | filtered registry returns typed `tool_unavailable` for side-effect calls, `unknown_tool` for forged names; real offline `run --mode plan` completed with no fixture writes or command events | verified |
| Workspace path and symlink guard | `src/forgecode/security/workspace.py` | CLI parent traversal and direct guard rejection; existing symlink/junction entries are rejected lexically, metadata errors now fail closed; OS link creation unavailable here | partial |
| Read/list/search UTF-8 tools | `tools`, `src/forgecode/tools/filesystem.py` | 150 UTF-8 files indexed/searched; list limit 100 truncates with omitted count; >2 MB, invalid UTF-8, traversal, and read/write race inputs fail closed; broader platform race stress pending | partial |
| Structured multi-file patch with atomic write | `apply_patch`, `tools/patch.py` | malformed second hunk is rejected pre-write; injected second-file I/O failure returns `write_failed`, rolls back first file, and preserves both originals | verified |
| Command risk classes, approval, timeout and output limits | `run_command`, `tools/shell.py` | dangerous classification, deny approval, 1 MiB output truncation, and typed timeout observed | verified |
| Secret redaction and privacy boundary | `security/redaction.py`, `privacy.md` | named/bearer/bracketed secrets redact correctly; review scanner scans tests with a narrow fixture-value allowlist and repository review has zero findings | verified |
| Session JSONL persistence, checkpoints and recovery | `storage/`, `session`, `sessions` | forced process-tree termination left a durable checkpoint; a fresh `run --resume --dry-run` returned bounded `recovery_preview` (`state=discovering`) with no side effects; stale-file and corruption checks remain fail-closed; full resumed execution still pending | partial |
| Transactions, hash conflict and undo | `transaction`, `rollback` | dry-run conflict probe | verified |
| Provider protocol, retry/SSE validation and cancellation | `models/`, `agent/` | direct normal completion plus malformed JSON, duplicate `[DONE]`, and non-finite SSE rejection | verified |
| Strict JSON/JSONL machine contract and exit codes | global `--json/--jsonl` | 13-command success/error matrix emits one parseable JSON line, stable 0/2 codes, and no traceback; exhaustive provider/runtime matrix pending | partial |
| Rules and scoped `AGENTS.md` precedence | `rules`, `rules.py` | nested-rule smoke | verified |
| Explicit context references and bounded context index | `context`, `context/index.py` | index/search/path-boundary smoke | verified |
| Structured plans and interactive controls | `plan`, `chat` | direct dispatcher and controller probes; bounded FIFO/pause/cancel verified | verified |
| Skills manifest validation and bounded execution | `skills`, `skills.py` | list/check malformed manifest | verified |
| Provider diagnostics and profile selection | `provider`, `config profiles` | `provider health/list` and `config profiles --json` pass offline | verified |
| Lifecycle hooks with fail-closed behavior | `hooks.py` | direct exception, timeout, recursion, cancellation and redaction probes | verified |
| Named test profiles and bounded verification | `test`, `testing.py` | direct runner probe: plan=skipped, deny=denied, allow=passed (exit 0); no pytest command used | verified |
| Evidence-driven review and export verification | `review`, `review.py` | repository review scans tests with a narrow fixture-value allowlist, reports 0 findings/exit 0; clean temporary workspace review/export also pass | verified |
| Incremental context extensions | `context complete`, `context/repository.py` | temporary repository update/delete/add stress: digest update, removal, bounded search, and diagnostics all passed | verified |
| Session tree/clone/import and trajectory evaluation | `session tree`, `eval` | canonical cross-workspace import and byte mutation rejection verified; synthetic valid lifecycle plus verification event returns CLI `eval` status `completed`, score 1.0; full replay semantics remain intentionally absent | partial |
| Interactive pause/resume/cancel and Escape handling | `chat`, `interactive_service.py` | active controller cancellation stops a live worker and drains safely; scripted JSONL Escape/EOF, help, mode validation, unknown command, and quit all terminate cleanly; live PTY provider cancellation pending | partial |
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
  The repository-wide scan now also passes: tests are scanned with a narrow,
  value-based fixture allowlist rather than being skipped wholesale.
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
  must fail closed. Checkpoint corruption and process interruption are also
  rejected or surfaced through bounded recovery previews; full resumed
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
- Review scanner hardening: tests remain in the production secret scan, while
  a value-based, test-directory-scoped fixture allowlist removes only known
  fake credentials; `review latest --no-verify-files` returns `ok=true`, exit 0
  with zero findings on the repository.
- Recovery boundary probe created a synthetic `acting` checkpoint with a file
  fingerprint. `run --resume --dry-run` returned exit 3 and a typed
  `recovery_conflict`; after modifying the file, the report additionally named
  `file fingerprint changed since checkpoint`. This proves fail-closed stale
  recovery, but not automatic recovery after an OS/process crash.
- Tool-policy fuzz probe sent a forged tool name, a policy-removed side-effect
  tool, and a malformed argument object. The registry returned bounded typed
  errors (`unknown_tool`, `tool_unavailable`, and argument validation) without
  invoking a tool. A 150-file UTF-8 fixture confirmed search completeness and
  list truncation (`count=100`, `omitted=50`); invalid UTF-8 and traversal were
  rejected without traceback. Concurrent mutation races remain unverified.
- Atomic patch probe supplied a valid first-file hunk followed by a mismatching
  second-file hunk. The tool returned `patch_invalid` before approval/write and
  both files retained their original bytes. CLI contract probes covered one
  success and four failure cases; each failure produced exactly one JSON error
  envelope and exit code 2 (no traceback).
- Concurrent persistence probe used eight threads appending 25 events each to
  one session. All 200 events were retained, with zero read issues and zero
  append errors, confirming the interprocess/thread lock on this platform.
- Interactive controller probe ran a live blocking worker, issued
  `cancel()` while active, released the worker, and observed clean transition
  to inactive with no queued follow-up execution. This validates controller
  cancellation; terminal Escape delivery to a real provider worker remains
  platform/integration work.
- A full offline demo reached `completed`; subsequent `run --resume --dry-run`
  returned an inspect-only `recovery_preview` with no pending actions and did
  not execute tools. This confirms completed sessions are safe to inspect;
  actual `--fork` execution remains intentionally separate.
- Trajectory success probe built a lifecycle-valid session (`created` →
  `discovering` → `planning` → `completed`) with an explicit successful
  verification event. `forgecode eval --json` returned status `completed`,
  score `1.0`, and exit code 0; an invalid lifecycle transition was correctly
  classified as `recovery_required`.
- File-read race probe continuously rewrote a 100,000-byte file while issuing
  100 reads; all attempts failed closed with the file-changed check (no mixed
  content was returned). Separate 2,000,001-byte and invalid-UTF-8 fixtures
  returned bounded `ValueError` messages and no traceback.
- CLI contract matrix covered `tools`, `config validate`, invalid session/list
  limits, missing session export/eval, conflicting review artifacts, and an
  invalid regex. Every invocation produced exactly one parseable JSON line,
  expected exit code (0 for success, 2 for client/input errors), and no
  traceback. Provider/network and every subcommand combination remain outside
  this bounded matrix.
- Fault-injection patch probe replaced the atomic writer to fail on the second
  file. The tool returned `write_failed` with `rolled_back=true`; both files
  matched their original bytes, confirming multi-file rollback on I/O error.
- Workspace alias handling was tightened so metadata/permission failures while
  checking a path are treated as unsafe (missing components remain valid for
  creation). Symlink creation was attempted but denied by this Windows
  environment, so the OS-specific positive rejection path remains noted.

- Final consistency gate passed: a fresh offline demo completed with 108
  events; repository review scanned 137 text files with zero findings;
  `doctor --json`, Python compileall, and diff checks passed without pytest.
- Review reverse probe placed an unlisted long token in `tests/suspicious.py`;
  the scanner correctly returned one high-severity finding and exit 1. This
  confirms the fixture allowlist does not suppress arbitrary test credentials.
- Checkpoint corruption probe wrote malformed JSON, and `CheckpointStore.load`
  returned a bounded `ValueError`; a later save refused to overwrite the
  unreadable checkpoint. This preserves fail-closed recovery state.
- Final offline gate used `run --demo --no-verify --max-steps 8`: the run
  completed with 108 session events and zero read issues; `doctor --json`,
  compileall, and `git diff --check` also passed. No pytest command was run.
- Plan-mode integration probe ran the offline demo with `--mode plan`; it
  completed successfully, created no demo fixture files, and recorded only a
  read-only `workspace_summary` tool result (no command or write events).
- Scripted `chat --jsonl --mode plan` input containing Escape, `/help`, an
  invalid `/mode`, an unknown command, and `/quit` produced six bounded JSONL
  records, exit 0, and no traceback. Active-provider PTY Escape delivery is
  still environment-dependent and remains a separate gap.
- Process-tree crash probe terminated an in-flight offline run after checkpoint
  creation. A fresh process found the durable JSONL/checkpoint and
  `run --resume --dry-run` returned `recovery_preview` in `discovering` state
  with no side effects. The interrupted atomic checkpoint write left an orphan
  `.tmp` file in the ignored runtime directory; recovery ignored it safely.

- Follow-up CLI gate (2026-08-29): `tools --json`, `config validate --json`,
  `context complete ../ --json`, and `session tree --limit 0|201 --json`
  each emitted one bounded JSON error/success envelope with the documented
  exit code; both offline demo modes (`run --demo --no-verify --max-steps 8`
  and `--mode plan`) completed with exit code 0. Generated demo fixtures were
  removed after the check. This does not upgrade the remaining partial rows:
  symlink-positive tests, automatic post-crash continuation, exhaustive
  provider/exit-code combinations, clone replay, live online cancellation,
  and real-PTY Escape cancellation still lack authoritative end-to-end
  evidence.

- Session branch boundary gate (2026-08-29): cloning a real session at
  sequence `0` succeeds with an explicit `replay=false` parent record;
  negative sequence input returns `invalid_session`/exit `2`, and bounded
  `session tree --limit 5` returns parseable node/edge metadata. Clone is
  therefore an inspect-only evidence branch by design; automatic replay of
  recorded side effects is not an implemented capability.

- Tool-policy matrix probe (2026-08-29): seven combinations covering no
  narrowing, allowlist, exclusion, `--no-tools`, duplicate names, overlap,
  and unknown names passed or failed closed as expected through
  `parse_tool_policy_options`; no tool was invoked during the probe.

- Final clean-workspace gate (2026-08-29): removed only the identified
  ignored demo transaction manifest/blob left by the offline fixture run;
  `review --json --no-verify-files` then scanned 137 text files and 102 Python
  files with zero findings and exit `0`. `doctor --json`, `compileall`, and
  `git diff --check` also passed. This cleanup is runtime-state maintenance,
  not a change to the tracked source implementation.

- Review recheck note (2026-08-29): an earlier review failure was reproduced
  as a stale transaction hash conflict after deleting demo fixtures; once the
  generated manifest/blob were removed, the same repository review passed.
  This demonstrates that review fails closed on stale transaction state and
  does not silently treat a missing/changed file as clean.

- Documentation accuracy correction (2026-08-29): README now qualifies Escape
  cancellation for live online providers/PTYs, matching the `partial` audit
  row; scripted/controller cancellation remains supported.

- Documentation consistency check (2026-08-29): README review guidance now
  distinguishes narrowly allowlisted known fixtures from arbitrary token-like
  test content and stale transaction hashes; this matches the reverse-probe
  and clean-repository review results.

- Symlink capability probe (2026-08-29): creating a temporary file symlink
  raised `OSError` under the current Windows account before the guard could
  receive it. Existing lexical/metadata checks remain covered by direct
  rejection probes; a positive OS-link rejection test must be repeated under
  an account with symlink privilege.

## 2026-08-29 interactive UX additions

- Added interactive `/connect`, which explicitly prompts for API endpoint,
  model, and visible API key; values remain process-local and are never
  persisted. Profile inspection remains available through CLI config commands,
  while the interactive `/model` command is intentionally not exposed.
- Added `/clear` (screen reset only), a compact text ready banner, and a
  black-background input bar on interactive terminals. JSON/JSONL transports
  remain prompt-free and machine-readable.
