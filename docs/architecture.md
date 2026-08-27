# ForgeCode Architecture

ForgeCode is a small provider-neutral local coding agent. The repository owns
the protocol conversion, conversation history, tool execution, loop
termination, error propagation, and safety boundary. It does not wrap a
ready-made agent framework or a hosted file/code-execution product.

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

## Durable transactions and streaming

Writes and patches prepare a versioned manifest before mutation. Before bytes
are stored as SHA-256-addressed blobs under ignored `.forgecode/transactions`;
only hashes and bounded previews reach sessions/review. Commit validates after
hashes. Undo rechecks every current after hash, obtains approval, restores all
targets using temporary atomic replacements, and creates a second transaction.
An external edit, missing/corrupt blob or repeated undo is a conflict and is
never overwritten with `git reset` or checkout.

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
and next action, with a finite repair budget.

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

The project intentionally does not implement IDE UI, autocomplete, browser or
computer control, voice, MCP marketplace, cloud execution, worktrees,
parallel subagents, background scheduling or enterprise governance. Model
providers, registry and safety contracts leave room for later extensions, but
the current assessment deliverable focuses on one auditable local agent and a
repeatable offline coding task.
