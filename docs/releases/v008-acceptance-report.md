# ForgeCode v0.0.8 acceptance report

Date: 2026-08-28 (Asia/Shanghai)
Scope: release-candidate implementation after the v0.0.7 baseline.

This report records fresh, offline, bounded acceptance evidence. All temporary
workspaces and runtime ledgers were outside the repository or under ignored
`.forgecode/` directories. The report intentionally excludes absolute local
paths, credentials, goal prompts, raw session/checkpoint/backup contents and
large command output. Hashes below are SHA-256 evidence, not source contents.

## Automated release gate

| Command | Exit | Bounded result |
| --- | ---: | --- |
| `uv sync` | 0 | Environment resolved from the lock file |
| `uv run python -m compileall -q src tests` | 0 | Source and tests compiled |
| `uv run pytest -rs` | 0 | **335 passed, 8 skipped** in 112.61 s; 2 non-fatal PytestCollectionWarnings |
| `uv run forgecode doctor --jsonl` | 0 | `ok=true`, status `ready`, no provider request |
| `uv run forgecode provider health --json` | 0 | `network_request=false`; capability metadata only |
| `uv run forgecode config validate` | 0 | configuration valid; credentials environment-only |
| `uv run forgecode review --jsonl` (repository root) | 1 | expected deterministic findings for 18 credential-shaped test literals; values redacted and no real credential was present |
| `uv run forgecode --help` | 0 | command groups include `test`, `review`, `context`, `skills`, `transaction` |
| `uv run forgecode test --help` | 0 | list/show/run, plan/act, approval, timeout and session options present |
| `uv run forgecode context --help` | 0 | index/search/show/clear/explain/diagnostics options present |
| `uv run forgecode skills --help` | 0 | list/check/show/run options present |

The eight skips are platform-conditional symlink/junction cases. This Windows
process cannot create symlinks, so the tests report `symlink creation is
unavailable`/`symlinks unavailable`; no test was muted for a product failure.
The two warnings only concern pytest attempting to collect imported dataclass
helpers (`TestProfile` and `TestProfileRunner`); they do not affect execution.
Configuration and named test-profile TOML inputs are capped at 1,000,000 bytes;
parser recursion failures and flat JSON scalar floods are covered by focused
regressions and become ordinary bounded errors.
Recovery prompt assembly also caps recovered evidence to the plan schema bound
while retaining the operator's follow-up, so a long compacted session cannot
turn a safe fork/resume into an unhandled plan-validation failure.

## Fresh integrated offline run

Command: `forgecode --workspace <fresh> run --demo --auto-approve --jsonl`
Exit: `0`
Run id: `6849afb8727246ef9602c1d16d60d8a5`
Result: `state=completed`, `verification_ok=true`, `audit_complete=true`,
`stopped_reason=model_finished`
Output: 90 parseable JSONL records (87 event records), 0 stderr bytes.

The event stream included the expected lifecycle, Plan/Act, approval,
repository snapshot, failing command (`exit_code=1`), patch preview/commit,
passing verification (`exit_code=0`), transaction commit and final result.
There were 24 checkpoint events and 16 state-transition events; no event was
replayed after the side effect.

The committed transaction and review evidence were:

| Evidence | Value |
| --- | --- |
| transaction id | `d236eab8b68e46128be7c207504e6286` |
| changed path | `demo_calculator.py` |
| before SHA-256 | `e1a894022d1a082987b87adecb623438c9e386d86b2b621cff4a5fe7fdf7edc8` |
| after/current SHA-256 | `ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772` |
| rollback preview | available; parent transaction remained hash-checked |
| review report id | `412b74cbaa8043a9b9e2888038997f7f` |
| review session sequence | 121; no read issues |
| review checks | `forbidden_paths=pass`, `secrets=pass`, `suspicious_commands=pass`, `syntax=pass` |
| review exit | `0` |

The report’s transaction verification recorded the final pytest command with
exit code 0 and a bounded one-test pass preview. Review status was derived from
durable evidence and checks; model prose did not mark it successful.

## Plan, session and recovery path

On a separate fresh workspace, `run --demo --mode plan --auto-approve --jsonl`
exited `0` with run id `3ebcdc9d30424483be01530a24b5d7a8`. It created only the
ignored runtime ledger/session files: no fixture, source or test file was
written, and verification was explicitly recorded as skipped for plan mode.

The Act session above was then exercised through the production session
commands. `session show` and `session inspect` both exited `0`; `session
compact` appended a deterministic summary (source sequence `1-121`, 30,427
to 8,613 characters, 97 events omitted) without rewriting the prefix; and
`session fork` exited `0` with child run id
`79486e82ea3843e395d291f4a3262123`. The committed transaction was safely
undone with `transaction d236eab8b68e46128be7c207504e6286 --execute
--auto-approve`, producing child transaction
`346924dfbdbcd107abfbfc6c3382201d`. A repeated inspection correctly returned
recovery/conflict exit code `3` rather than claiming another undo.

On another clean workspace, an external append to `demo_calculator.py` was
made after the demo commit and before undo. Undo exited `3` for transaction
`6e010a6e90b74125b53ab9375d0984b4`; the appended line and its resulting file
digest remained intact. This proves the current-bytes hash check protects an
external edit.

## Skills and context path

The fresh workspace contained a read-only Markdown skill and an executable
Python skill. `skills list`, `skills check` and `skills show` each exited `0`.
Running the executable without `--approve` exited `1` with `approval_required`;
the approved invocation exited `0` and returned only the bounded output
`approved`. The skill manifest used schema version `1`, a relative entry, an
explicit command side-effect and the normal timeout/environment boundary.

The context index accepted a UTF-8 multilingual Python file and combined glob,
regex, language, symbol and line filters in one search (`count=1`). The
explanation listed `.env` as `sensitive` and `.forgecode` as an ignored
directory. After an external edit, `context diagnostics --jsonl` exited `0`
with `reason=digest_changed` and both expected/observed SHA-256 values; no
stale snippet was returned.

## Independent JSONL check

A second clean demo run (run id `3156b492dd744de689b0ebf108d9e983`) was
captured with stdout and stderr separated. It exited `0` and emitted 90 lines
(87 lifecycle events, 2 expected non-success event envelopes and 1 final
result). An independent parser validated every stdout line as JSON with
`schema_version`, `kind`, `ok` and `command`; malformed-line count was zero and
stderr was 0 bytes. The expected failing pre-patch command remained an evidence
event and did not prevent the final verified result.

## Review artifact integrity

`review --export review-artifact.json --jsonl` exited `0`, produced a 12,962-byte
artifact, and returned report id `8e208d3cc6cd460daef0cd9605dbb5ac`.
`review --verify review-artifact.json --jsonl` also exited `0` and returned the
same report id. The envelope and report digests were:

- artifact SHA-256:
  `481dcbe4ab449356bd84004ab7be329bc26de5bdff2f6394c1bb6048f43442d8`
- report SHA-256:
  `d9bf12588bab2e4f2f084cc22be25152ee02d145a7ec600f8879892197d16c7e`
- verified current file digest:
  `demo_calculator.py=ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772`

The review tests additionally mutate an exported artifact/file and assert a
stale or digest-mismatch failure (exit code 3); raw backup bytes are never
embedded in the artifact.

## Named test-profile acceptance

Fresh `.forgecode/tests.toml` profile `quick` used the argv command
`["python", "-c", "print('profile-ok')"]`, `cwd="."`, a 10-second timeout,
no inherited secret variables and expected exit code `[0]`.

| Invocation | Exit | Evidence |
| --- | ---: | --- |
| `test list --jsonl` | 0 | strict envelope; source `.forgecode/tests.toml` |
| `test show quick --jsonl` | 0 | argv, quota and approval fields exposed |
| `test run quick --jsonl` | 0 | evidence `d9ca5f5fbe2f419f8d61ecb71c55063f`, `verification_status=passed`, child exit 0 |
| `test run quick --mode plan --jsonl` | 1 | evidence `d312a3789ffb4b43b4d702c389bbcb55`, `approval=mode_denied`, `verification_status=skipped`; no process spawned |

The passing evidence had equal before/after workspace fingerprints and bounded
stdout (`profile-ok`); setup/teardown failures, cancellation, timeout and
unresolved termination are covered by the profile and cancellation regression
tests and cannot become a pass.

## Resilience and machine contract coverage

The focused suites cover provider request/attempt identity, retry/backoff,
malformed or incomplete SSE, cancellation before tool dispatch, non-cooperative
worker deadlines, unresolved recovery checkpoints, cross-process session and
transaction locks/CAS, partial/hash-conflict undo, context stale digests,
skill precedence/state migration, hook timeout/cleanup/correlation, and review
security findings. New JSONL commands emit one bounded envelope with
`schema_version`, `kind`, `ok`, `command` and exactly one of `data` or `error`;
progress and approval prompts remain on stderr. Exit codes are 0 (success), 1
(execution/audit/test failure), 2 (input/configuration), 3 (recovery or digest
conflict), and 130 (cancellation).

## Scope boundary

F23–F27 remain deliberate post-release boundaries: no IDE UI/autocomplete,
browser or computer control, remote MCP marketplace, cloud execution,
worktrees, parallel subagents, background scheduling or enterprise governance.
The command classifier and workspace checks are defense-in-depth approval
controls, not an operating-system sandbox.

Before the release commit, synchronize the version files to `0.0.8`, rerun the
release gate, inspect staged/generated output for secrets and private paths, and
then create the single `v0.0.8` commit/tag described in `docs/VERSIONING.md`.
