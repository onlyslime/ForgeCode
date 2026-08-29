# Implemented Features Audit

This file is the maintained inventory of capabilities ForgeCode claims to
provide. A capability is marked **verified** only after a direct CLI/API smoke
check and source-path inspection; **partial** means the boundary works but the
full claim still has a limitation. Update this table whenever behavior changes.

| Capability | Entry point / source | Manual audit evidence | Status |
|---|---|---|---|
| Plan/Act mode and fail-closed tool policy | `plan`, `src/forgecode/agent/loop.py` | policy output shows side-effect tools disabled without trust; forged-call stress pending | partial |
| Workspace path and symlink guard | `src/forgecode/security/workspace.py` | `context complete ../` rejected with exit 2; symlink stress pending | partial |
| Read/list/search UTF-8 tools | `tools`, `src/forgecode/tools/filesystem.py` | source path identified; fixture/limit stress pending | partial |
| Structured multi-file patch with atomic write | `apply_patch`, `tools/patch.py` | source path identified; valid/invalid patch stress pending | partial |
| Command risk classes, approval, timeout and output limits | `run_command`, `tools/shell.py` | source path identified; safe/dangerous/timeout stress pending | partial |
| Secret redaction and privacy boundary | `security/redaction.py`, `privacy.md` | review found test fixtures are flagged as token-shaped; runtime redaction stress pending | partial |
| Session JSONL persistence, checkpoints and recovery | `storage/`, `session`, `sessions` | `sessions --json` read existing bounded records; crash/recovery stress pending | partial |
| Transactions, hash conflict and undo | `transaction`, `rollback` | dry-run conflict probe | verified |
| Provider protocol, retry/SSE validation and cancellation | `models/`, `agent/` | provider health is offline/no-network; malformed-stream stress pending | partial |
| Strict JSON/JSONL machine contract and exit codes | global `--json/--jsonl` | doctor/config/RPC responses parsed as JSON; full exit matrix pending | partial |
| Rules and scoped `AGENTS.md` precedence | `rules`, `rules.py` | nested-rule smoke | verified |
| Explicit context references and bounded context index | `context`, `context/index.py` | index/search/path-boundary smoke | verified |
| Structured plans and interactive controls | `plan`, `chat` | help available; interactive FIFO/pause/cancel stress pending | partial |
| Skills manifest validation and bounded execution | `skills`, `skills.py` | list/check malformed manifest | verified |
| Provider diagnostics and profile selection | `provider`, `config profiles` | `provider health/list` and `config profiles --json` pass offline | verified |
| Lifecycle hooks with fail-closed behavior | `hooks.py` | direct exception, timeout, recursion, cancellation and redaction probes | verified |
| Named test profiles and bounded verification | `test`, `testing.py` | help/schema smoke (pytest intentionally not run) | partial |
| Evidence-driven review and export verification | `review`, `review.py` | fresh workspace review smoke | partial |
| Incremental context extensions | `context complete`, `context/repository.py` | source inspection; large-tree stress pending | partial |
| Session tree/clone/import and trajectory evaluation | `session tree`, `eval` | help and empty-session boundary probes | partial |
| Interactive pause/resume/cancel and Escape handling | `chat`, `interactive_service.py` | source inspection; PTY stress pending | partial |
| RPC server plus Python/Node embedding | `rpc`, `rpc.py`, `sdk/node/index.mjs` | JSONL request/response smoke | partial |
| Runtime tool narrowing (`--tools`, `--exclude-tools`, `--no-tools`) | CLI/config policy | policy help/source identified; deny-path matrix pending | partial |
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
