# ForgeCode Architecture (v0.0.9)

ForgeCode is a small provider-neutral local coding agent. The repository owns
the protocol conversion, conversation history, tool execution, loop
termination, error propagation, and safety boundary. It does not wrap a
ready-made agent framework or a hosted file/code-execution product. Version
0.0.8 adds named test profiles, evidence-driven review, cancellation/deadline
propagation, and a strict machine-output contract without changing that trust
boundary.

## Components and data flow

```text
CLI parser/renderer
  -> application services (run/session/transaction/interactive)
  -> typed config (CLI > ignored TOML > environment > defaults)
  -> scoped Rules + explicit References + structured TaskPlan
  -> AgentLoop
       -> ContextBuilder (system policy + bounded history)
       -> ModelProvider
            -> OpenAICompatibleProvider (urllib + Chat Completions)
            -> DemoProvider (deterministic offline calls)
       -> ToolRegistry (schemas + mode filter + dispatch)
            -> WorkspaceGuard (resolved paths and symlink boundary)
            -> read/list/search/summary (read-only context)
            -> write_file/apply_patch (approved atomic writes)
            -> run_command (risk + approval + timeout)
       -> RepositoryMap/ContextPlan (bounded deterministic snapshot)
       -> ContextIndex (ignored incremental metadata/search cache)
       -> SkillLoader/SkillRegistry + HookRegistry (validated, audited extensions)
       -> TestProfileLoader/Runner (strict argv profiles + bounded evidence)
       -> ReviewBuilder (ledger join + deterministic security checks/artifacts)
       -> SessionStore + CheckpointStore (bounded redacted JSONL + fingerprints)
       -> TransactionStore (bounded manifests + ignored content-addressed blobs)
```

For each model turn, `AgentLoop` sends the system/user intent and the most
useful bounded recent messages to a provider. A provider returns a neutral
`Message` and zero or more `ToolCall` values. The registry validates and
dispatches every call, then the loop appends a tool result with the matching
`tool_call_id`. Tool errors, approval denials, non-zero exits and timeouts are
ordinary model-visible results. The loop stops on a final response, provider
or protocol error, interruption, repeated-call limit, verification failure or
the configured step limit.

`cli.py` is a stable compatibility entry point. Parser and command dispatch
live in `application/commands.py`; reusable `RunService`, `SessionService`,
`TransactionService` and `InteractiveSession` return typed values rather than
owning a second execution engine.

## Rules, references and structured plans

`RuleEngine` loads root and target-parent-chain `AGENTS.md` files in stable
order. Each source carries workspace-relative path, scope, priority, digest,
size and omission metadata. Rules are untrusted context: they cannot enable a
tool, approve an operation, cross the workspace, or alter command hard blocks.
A fresh fingerprint check immediately before side effects catches rule changes
after planning.

`ReferenceResolver` parses explicit `@file`, quoted paths, bounded directories
and read-only `@git:status|diff|log`. It rejects private runtime, goals,
credentials, binary/non-UTF8 and escaped paths. Each item has a digest, size,
language, priority and truncation state. Git runs only fixed argument arrays
with timeout/output caps and never changes repository state.

`TaskPlan` is a versioned DAG of stable `PlanItem` ids with dependencies,
risk, expected files/commands, acceptance criteria, status and evidence.
Invalid cycles/transitions are rejected. Rules/reference/checkpoint
fingerprints can mark a plan stale; Plan -> Act requires a fresh approval.

## Plan/Act safety boundary

`AgentMode.PLAN` and `AgentMode.ACT` are state in `ToolContext` and
`AgentLoop`, not just prompt wording.

- Plan mode exposes only `list_files`, `read_file`, `search` and
  `workspace_summary`. The registry rejects a side-effecting call with a
  structured `mode_denied` result; write, patch, command and verification
  paths also defend themselves when called directly.
- Act mode exposes every tool, but each write, patch and command still needs
  an approval decision. `--auto-approve` changes only that decision policy.
- The system message states the active mode, available tools and verification
  rule. A plan run records a copyable plan summary and explicitly skips
  verification. Session events include `mode`, `mode_denied`, approval and
  final results.

This two-layer design protects against both an over-eager model and a caller
that bypasses the schema filter and invokes a tool object directly.

## Workspace and file operations

`WorkspaceGuard` resolves every user path and rejects absolute paths outside
the root, `..` traversal and symlink/junction escapes. Listing and searching
use stable ordering, bounded file/match/line sizes and built-in plus basic
`.gitignore` exclusions. `.env`, credentials, key/certificate files, generated
directories and session/goal data are not used as default context.

`write_file` means complete UTF-8 replacement. It validates the destination,
asks for approval, writes an fsynced same-directory temporary file and uses an
atomic replace.

`apply_patch` is a separate structured editing operation. It parses unified
diffs and the `*** Begin Patch` form; supports multiple files, hunks, line
offsets, creation and explicitly opted-in deletion; rejects malformed,
ambiguous, duplicate, binary, oversized or out-of-workspace targets. All
targets are read and all hunks are applied in memory before one approval. A
bounded unified preview is shown in approval/session/CLI output. If writing
one target fails, already-written targets are restored from their original
bytes; the implementation documents that an abrupt machine/filesystem failure
outside the process cannot be made a full transaction by a standard file API.
CRLF and no-final-newline text are preserved.

## Command policy

`run_command` runs with the workspace as cwd and a 1--120 second timeout.
Output is bounded and returned with stdout, stderr, exit code, duration,
timeout and truncation metadata. A conservative classifier labels commands as
`normal`, `filesystem_destructive`, `privilege_or_system`,
`network_or_remote` or `repository_irreversible`.

Shutdown/reboot, disk format, root deletion, `git reset --hard`, forced
`git clean` and force-push patterns are hard-blocked; auto-approve cannot
override them. Other mutation, privilege, network/install and Git operations
are surfaced for approval. The classifier is a heuristic, not a sandbox.
The child environment removes variables whose names suggest API keys, tokens,
secrets, passwords or cookies. Timeout handling attempts to terminate the
process tree (Taskkill on Windows, process group on POSIX).

## Context budget and audit log

`ContextBuilder` keeps system and user intent, truncates oversized messages,
prefers recent complete tool context and inserts an omission marker when the
character budget is exceeded. `ToolRegistry` bounds and redacts tool output
before it reaches the model. `SessionStore` appends JSONL events for mode,
messages, calls, approvals, tool results, verification, errors and stop
reasons. Nested sensitive keys and credential-shaped text are redacted, and
large strings/collections are bounded. v1 events carry a run id, sequence,
mode and schema version; corrupt lines are reported with safe partial reads.
Checkpoints capture file SHA-256/size/mtime fingerprints and pending actions.
Resume validates those identities and requires a fresh preview/approval; it
never treats session text as trusted executable input or silently replays a
side effect. `inspect`/`map` exposes the same deterministic repository map and
budgeted relevance planner as a read-only context layer.

`ContextCompactor` derives factual sections in fixed priority order (safety,
rules, current intent, plan, checkpoint, transactions, failures/verification,
references, recent history). It appends `context_compacted`; it never deletes
or rewrites original JSONL. `SessionContextRebuilder` reconstructs bounded
provider-neutral messages and evidence. Recorded tool calls are descriptions,
not instructions, so resume never replays them. Completed sessions are
inspect-only unless explicitly forked to a new run id with a parent link.

### Incremental context index and extensions

`ContextIndex` is a local JSON cache under `.forgecode/context-index.json`.
It records relative path, size, mtime, SHA-256, language, line count, symbols
and sensitive/binary flags.  Rebuilds are deterministic and atomic; malformed
or stale entries are diagnosed/rebuilt, and snippets are returned only after a
fresh digest check.  `context index|search|show|clear` are read-only CLI
operations.  The index is a cache rather than authorization and is never
committed or sent as an unbounded model prompt.

`SkillManifest` and `SkillLoader` accept explicitly located Markdown or
manifest files with strict ids, semantic versions, schemas, side-effect levels,
approval requirements and quotas.  Markdown skills can enrich a prompt; an
executable skill has no executor unless an application supplies one after
approval.  `HookRegistry` provides bounded before/after tool and model events;
hooks are observers by default, fail-closed only when declared, and recursion
or permission changes are blocked.  Hook and index evidence is appended to the
same session audit stream.

## Named test profiles and verification evidence

`TestProfileLoader` reads `.forgecode/tests.toml` (with the compatibility name
`.forgecode/test-profiles.toml`) through a 1,000,000-byte TOML input bound and
rejects unknown fields, shell-string
commands, unsafe working directories, credential-bearing environment names,
non-finite values and oversized quotas.  A profile command, optional setup and
teardown are argv tuples executed with `shell=False`; only a small inherited
environment plus an explicit non-secret allow-list is passed to the child.
Each phase shares one deadline and independent stdout/stderr/total limits.
Success requires an explicitly listed exit code and successful setup/teardown;
timeouts, cancellation, unresolved process termination and approval failures
cannot become a pass.  `test list|show|run` uses `TestProfileRunner` and
appends a `test_profile_result` event containing digests and bounded previews.
Interactive `/test` remains a compatibility verifier for ad-hoc commands; it
uses the shared command policy, approval, timeout, revalidation and session
evidence path, but does not reinterpret a shell string as a named profile.

`ReviewBuilder` is a deterministic, model-independent evidence join.  It reads
the selected session and transaction manifests, links plan/reference/context,
test and hook events, reconstructs bounded diff hunks, and computes rollback
availability.  Four static checks (secret-shaped text, forbidden paths,
suspicious commands and Python AST syntax) return explicit `pass`, `fail`,
`skipped` or `error` statuses with finding ids and budgets.  An audit is passing
only when durable input is valid, checks do not fail, and no conflict remains.
`review --export` writes a size-bounded artifact bound to a workspace identity;
`--import`/`--verify` recompute the artifact and current-file SHA-256 values,
returning stale/tampered errors instead of accepting altered evidence.  Raw
session records and transaction backup bytes are never embedded in a report.

## Provider cancellation and machine contracts

`CancellationToken` is shared by the loop, synchronous tools, provider
adapters, test runner and SSE parser.  A run deadline is clamped at every
boundary.  Cooperative providers receive `ProviderContext`; a legacy or
non-cooperative provider runs in a bounded worker, and a result that outlives
the cleanup grace period is journaled as an unresolved attempt.  Provider
attempt/retry events carry request and attempt identities, outcome category and
retryability.  The loop checks cancellation again after a provider returns and
before dispatching any tool call, so a late response cannot cause a side effect.

Machine-facing commands support a strict JSONL envelope:
`schema_version`, `kind`, `ok`, `command`, and exactly one of `data` or `error`,
plus a bounded `exit_code`.  Progress, diagnostics and approval prompts are
sent to stderr.  Legacy `--json` aliases remain only where existing clients
depend on them; they cannot overwrite canonical envelope fields.

## Durable transactions and streaming

Writes and patches prepare a versioned manifest before mutation. Before bytes
are stored as SHA-256-addressed blobs under ignored `.forgecode/transactions`;
only hashes and bounded previews reach sessions/review. Commit validates after
hashes. Manifest, session and checkpoint writers use inter-process locks and
compare-and-swap/monotonic sequence checks, so a stale process cannot silently
overwrite newer evidence. Undo rechecks every current after hash, obtains
approval, restores all targets using temporary atomic replacements, and creates
a second transaction. An external edit, missing/corrupt blob or repeated undo
is a conflict and is never overwritten with `git reset` or checkout.

The optional SSE parser bounds bytes/events and assembles content and tool-call
fragments in memory. A tool call becomes a provider-neutral `ToolCall` only
after `[DONE]`, a valid finish reason, unique id/name and complete JSON object
arguments. Broken streams return errors before AgentLoop receives any call.

## Lifecycle, transactions and verification

Runs use a checked `RunState` machine (`created`, `discovering`, `planning`,
`awaiting_approval`, `acting`, `verifying`, `paused`, `completed`, `failed`,
`cancelled`, `recovery_required`). State transitions and model/tool/approval,
checkpoint, transaction and verification events share the monotonic session
sequence. Patches and writes expose transaction ids, before/after hashes and
bounded previews; approval-time fingerprints prevent overwriting an external
edit, and in-process failures restore already-written targets. Verification is
represented by typed results with exit code, streams, timeout, changed files
and next action, with a finite repair budget. A detached provider/process,
unresolved hook cleanup or pending side effect moves the run to recovery
evidence rather than `completed`.

## Reproducible demo

`DemoProvider` uses the same loop, schemas, registry and tools as an online
provider. In Act mode the CLI first creates a fresh, intentionally broken
calculator or JSON-config fixture through `write_file`; the model then
summarizes and reads it, runs a real failing pytest, applies an approved patch,
reruns pytest and passes a final verification command. `python -B` avoids
stale bytecode when a same-size source file is atomically replaced. Plan demo
creates no fixture and ends with a read-only plan. A user workspace containing
an existing fixture is rejected rather than overwritten.

## Current scope and limitations

The project intentionally does not implement IDE inline completion (the
read-only `context complete` path suggestion is bounded and advisory), browser or
computer control, voice, MCP marketplace, cloud execution, worktrees,
parallel subagents, background scheduling or enterprise governance. Model
providers, registry and safety contracts leave room for later extensions, but
the current assessment deliverable focuses on one auditable local agent and a
repeatable offline coding task.

## v0.0.9 long-run services

Before each provider request the loop measures serialized message and tool
argument size. Near the configured budget it appends an automatic
`context_compacted` event and rebuilds context from safety policy, user intent,
plan/verification evidence and a bounded recent window. The event carries the
source sequence range and summary fingerprint; it is evidence, never tool
authorization. `evaluation.py` computes a holistic trajectory score from the
same durable events. `session tree|clone|import` expose parent metadata while
explicitly avoiding side-effect replay. `context complete` is advisory and
every selected path still re-enters `WorkspaceGuard`.
