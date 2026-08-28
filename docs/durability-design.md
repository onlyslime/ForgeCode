# ForgeCode durability design (v0.0.8)

This document records the v0.0.5 reliability baseline, v0.0.6 durable
extensions, and v0.0.7 context/extension work. Version 0.0.8 adds strict test
profiles, evidence-driven review, cancellation propagation and machine-output
contracts. This is a design contract for the v0.0.8 release and its tests,
with runtime evidence recorded in the capability trace and acceptance report;
it is not a claim that a feature exists before its acceptance test passes.

## Run lifecycle

Every run has a typed `RunState`. Transitions are checked against one table;
terminal states cannot transition. Plan runs move through discovery and
planning, while Act runs may additionally wait for approval, act and verify.
Cancellation is distinct from failure, and an unsafe resume moves to
`recovery_required` rather than guessing.

```text
created -> discovering -> planning -> awaiting_approval -> acting
                                      ^                  |
                                      |                  v
                                  paused <--------- verifying

any active state -> failed | cancelled | recovery_required
planning/verifying -> completed
paused/recovery_required -> discovering (only after explicit safe resume)
```

`LoopResult` carries both the lifecycle state and a stable outcome code. The
CLI derives its exit status from that structured result.

## Durable event envelope

Sessions remain human-readable JSONL. Schema v1 contains `schema_version`,
`run_id`, monotonic `sequence`, UTC `timestamp`, `kind`, `mode`, bounded
`payload`, and optional operation/outcome/error fields. Writes are serialized
within a process, flushed and fsynced before the sequence is committed. Safe
reading reports corrupt line numbers and retains the valid prefix. Legacy
v0.0.4 three-field events are readable as schema 0 but are never treated as
instructions to execute.

Session output is recursively normalized, redacted and bounded. Paths,
exceptions, bytes, sets, non-finite numbers and cycles have deterministic
representations. A session write failure marks the run's audit as incomplete;
it cannot be reported as a fully successful audited run.

## Checkpoint and resume safety

A checkpoint stores the run state and mode, a hash-derived workspace identity,
bounded context summary, last confirmed call, pending actions and file
fingerprints (relative path, SHA-256, size and nanosecond mtime). Resume first
validates the run id, schema, sequence, workspace and every fingerprint.
Inspection and dry-run never execute a tool. Already committed effects are
not replayed; pending writes and commands require a new preview and approval.
Any mismatch defaults to `recovery_required`. Auto-approval cannot override a
recovery conflict.

## Retry and idempotency

Provider retries are limited to transport failures, HTTP 408/429 and 5xx.
They use bounded exponential backoff with jitter and emit one event per
attempt. A provider request may be retried because it has no local side
effect. A local tool call is never automatically replayed. Attempt evidence is
identified by the pair `(request_id, attempt_id)` so a provider that reuses a
per-turn id cannot hide a later request. If a worker times out or ignores
cancellation, its request is forced to `unresolved` even when a late success
marker arrives; recovery must inspect it explicitly.

## Change transactions

Patch parsing, path resolution, target reads and hunk application finish in
memory before approval. A typed change plan records transaction id, operation,
before/after fingerprints, encoding and newline style. Immediately before the
first write, targets are rechecked for optimistic-concurrency conflicts.
Writes use fsynced same-directory temporary files and atomic replacement.
Process-visible failures trigger reverse-order restoration and a rollback
event. Standard file APIs cannot guarantee atomicity across a machine crash,
disk failure or a hostile concurrent writer; these limits remain explicit.

In v0.0.6+, exact before bytes are written before mutation to content-addressed
blobs under ignored `.forgecode/transactions`. A bounded manifest links
before/after hashes, operation, run/plan ids, approval and verification.
Manifest writers use a cross-process lock and compare-and-swap state/sequence
checks; a stale writer cannot replace a newer record. Review and undo reopen
this ledger in a new process. Undo requires the current after hash, fresh
approval and all-target prevalidation; it creates a new transaction and marks
the parent undone. Corrupt/missing blobs, external edits and repeated undo fail
closed. Partial restoration records each operation and leaves unrelated
external edits untouched.

## Context and repository map

A deterministic repository snapshot follows built-in sensitive exclusions and
basic `.gitignore`, limits file count and bytes, reports omissions/errors, and
sorts all outputs. Context selection ranks current intent, checkpoints,
failures, pending calls, verification and relevant repository entries before
old conversation. Omissions are visible metadata, never silent.

The v0.0.7 `ContextIndex` is an ignored, atomically replaced JSON cache. Each
entry carries a digest and metadata; search re-reads and re-hashes a file before
returning a snippet, so an edited or renamed file becomes a miss rather than
stale model context. Corrupt indexes are rebuilt with a diagnostic report.
`SkillLoader` validates manifest ids, versions, schemas, permissions and
quotas. Markdown skills are read-only prompt data; executable entries require
an explicit executor and approval. `HookRegistry` records bounded lifecycle
observations and fail-closed errors without permitting recursion or authority
changes.

Compaction appends a deterministic factual summary and never rewrites the
original JSONL prefix. Context rebuild uses validated events/checkpoints and
treats previous tool calls only as evidence. Completed sessions are
inspect-only; explicit forks receive a new run id and parent sequence.

## Test-profile evidence

`.forgecode/tests.toml` is parsed as a strict schema (the input is capped at
1,000,000 bytes and parser recursion failures are reported as structured
configuration errors). Commands, setup and
teardown are argv arrays and run with `shell=False`; cwd must resolve inside the
workspace, inherited environment variables are reduced to a safe baseline, and
only an explicit non-secret allow-list may be added. Every phase shares the
profile deadline and bounded stream quotas. A profile passes only when setup,
main, teardown and the expected exit-code check all succeed. Approval denial,
Plan mode, cancellation, timeout, failed cleanup or unresolved termination are
recorded as non-passing `TestEvidence` and (when a session is supplied) a
`test_profile_result` event. Full stdout/stderr are represented by digests;
only redacted previews are persisted.

## Evidence-driven review and artifacts

`ReviewBuilder` reads validated session events and transaction manifests and
joins plan, references, context-index, test-profile, hook and diff evidence.
Its deterministic checks report explicit status and budgets for secret-shaped
text, forbidden paths, suspicious commands and Python syntax. A report is
passing only if the source records are complete, all applicable checks pass and
there are no hash or recovery conflicts. Exported artifacts contain relative
paths, event references and SHA-256 values—not raw session lines or backup
bytes—and are bound to a workspace identity. Import/verify recomputes both the
artifact digest and current file digests, returning stale/tampered rather than
silently accepting changed evidence.

## Cancellation, cleanup and machine output

`CancellationToken` and a monotonic deadline are propagated through providers,
test phases, synchronous tools and SSE chunk assembly. A detached or
non-cooperative provider is bounded by a cleanup grace period; its attempt is
marked unresolved and cannot authorize a tool dispatch. Hook callbacks have a
bounded timeout and optional cleanup; unresolved fail-closed cleanup becomes a
recovery issue, and cleanup executes at most once. New CLI JSONL commands use
the envelope `schema_version/kind/ok/command` plus exactly one `data` or
`error`; diagnostics and prompts go to stderr, preserving parseable stdout.

## Commands and verification

Commands retain the five stable risk classes. Hard-block patterns are checked
before approval. Each execution records an id, cwd relative to the workspace,
risk, approval, timing, exit status, timeout and bounded streams. Per-command
timeouts and the run deadline both apply; termination is best-effort for the
whole process tree on Windows and POSIX and failure to terminate is reported.

Verification uses a typed result and the same command policy. Plan mode never
executes verification. Act repair attempts are bounded, and file fingerprints
are compared around verification so external edits become conflicts. Named
profiles use the stricter argv runner described above; the legacy shell-string
verifier remains for backwards-compatible agent `--verify` commands.

## Compatibility and exit codes

Schema v1 readers accept legacy audit events for inspection/export but legacy
data cannot authorize a side effect. CLI exit codes are: 0 success/read-only
inspection, 1 run failure, 2 invalid input/configuration, 3 recovery conflict,
and 130 cancellation. Text and JSON modes share the same application services.
For new machine commands, `--jsonl` emits one strict envelope per line; a
failure has `ok=false` and an `error` object instead of a simultaneous success
`data` object. Legacy `--json` aliases are retained only for existing clients.
