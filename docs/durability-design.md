# ForgeCode durability design (v0.0.5)

This document records implementation decisions for the v0.0.5 reliability
milestone. It is a design contract for the code and tests, not a claim that a
feature exists before its acceptance test passes.

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
effect. A local tool call is never automatically replayed. Tool call ids and
fingerprints are tracked independently.

## Change transactions

Patch parsing, path resolution, target reads and hunk application finish in
memory before approval. A typed change plan records transaction id, operation,
before/after fingerprints, encoding and newline style. Immediately before the
first write, targets are rechecked for optimistic-concurrency conflicts.
Writes use fsynced same-directory temporary files and atomic replacement.
Process-visible failures trigger reverse-order restoration and a rollback
event. Standard file APIs cannot guarantee atomicity across a machine crash,
disk failure or a hostile concurrent writer; these limits remain explicit.

## Context and repository map

A deterministic repository snapshot follows built-in sensitive exclusions and
basic `.gitignore`, limits file count and bytes, reports omissions/errors, and
sorts all outputs. Context selection ranks current intent, checkpoints,
failures, pending calls, verification and relevant repository entries before
old conversation. Omissions are visible metadata, never silent.

## Commands and verification

Commands retain the five stable risk classes. Hard-block patterns are checked
before approval. Each execution records an id, cwd relative to the workspace,
risk, approval, timing, exit status, timeout and bounded streams. Per-command
timeouts and the run deadline both apply; termination is best-effort for the
whole process tree on Windows and POSIX and failure to terminate is reported.

Verification uses a typed result and the same command policy. Plan mode never
executes verification. Act repair attempts are bounded, and file fingerprints
are compared around verification so external edits become conflicts.

## Compatibility and exit codes

Schema v1 readers accept legacy audit events for inspection/export but legacy
data cannot authorize a side effect. CLI exit codes are: 0 success/read-only
inspection, 1 run failure, 2 invalid input/configuration, 3 recovery conflict,
and 130 cancellation. Text and JSON modes share the same application services.
