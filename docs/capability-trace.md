# ForgeCode v0.0.7 capability trace

This document is the implementation trace for the v0.0.7 release (building on
v0.0.6). It is
intentionally evidence-oriented: a capability is considered complete only
when its source, tests, and a runnable CLI/fresh-workspace scenario agree.

## Scope mapping

| Capability family | v0.0.6 baseline | v0.0.7 target/source | Evidence status |
| --- | --- | --- | --- |
| Provider-neutral loop, tool schemas, Plan/Act gate | `agent/loop.py`, `tools/base.py` | Preserve and integrate rule/reference/plan context | baseline regression + new integration tests |
| Workspace-safe read/write/patch/command | `security/workspace.py`, `tools/*` | Preserve; ledger will add durable evidence | baseline regression + transaction tests |
| Session JSONL/checkpoint/recovery | `storage/session.py`, `storage/checkpoint.py` | Rebuilder, compaction, fork and pending-action semantics | preserved from v0.0.6 |
| Scoped project rules | `rules.py` | AGENTS.md discovery, scope, digest, diagnostics | preserved from v0.0.6 |
| Explicit references and Git context | `references.py` | files/directories, bounded content, read-only Git | preserved from v0.0.6 |
| Structured plan | `plan.py` | schema, DAG, revision, status, evidence, stale | preserved from v0.0.6 |
| Interactive session | interactive service/REPL | slash commands including skills | v0.0.7 extension |
| Durable transaction/undo | in-process rollback metadata | persistent ledger, ignored backups, hash-checked undo | v0.0.6 implementation |
| Typed config/profile/policy | environment-only `Settings` | TOML + precedence + redacted `config show/validate` | v0.0.6 implementation |
| Streaming | synchronous Chat Completions | bounded SSE assembly and safe fallback | v0.0.6 implementation |
| Verification/observability | bounded verifier and session events | shared evidence aggregator and fault injection | v0.0.6 implementation |
| Incremental context index/search | repository map only | `context/index.py`: digest-checked JSON cache, deterministic search and CLI | v0.0.7 implementation + `tests/test_v007_extensions.py` |
| Skills and extension manifest | interactive slash commands | `skills.py`: strict Markdown/manifest loader and read-only registry | v0.0.7 implementation + extension tests |
| Lifecycle hooks | approval observers | `hooks.py`, ToolRegistry/AgentLoop before/after events | v0.0.7 implementation + hook tests |
| Provider capabilities/health | provider call only | `ModelCapabilities`, `provider health`, offline diagnostics | v0.0.7 implementation + provider tests |
| F23-F27 research extensions | intentionally out of scope | IDE/autocomplete, browser/computer control, cloud/worktrees, multi-agent/background orchestration, enterprise governance | post-v0.0.6 |

## Trust and data boundaries

1. System safety, explicit user approval, `WorkspaceGuard`, command hard
   blocks and hash conflicts outrank all project prose and provider output.
2. Rules, session text, plans loaded from disk, and model responses are
   untrusted context.  They can describe work but cannot grant capabilities.
3. Explicit references are preferred over repository-map hints, but remain
   bounded by sensitive-path filters and the context budget.
4. A plan is a typed, versioned DAG.  Plan -> Act requires a fresh approval;
   stale rules/context/checkpoint fingerprints invalidate that handoff.
5. Transaction raw bytes live only in ignored runtime storage.  Session and
   export events contain hashes, bounded previews and evidence, never raw
   backup content.

## Event and exit-code vocabulary

Events are append-only JSONL envelopes with schema version, run id, monotonic
sequence and bounded/redacted payload.  New implementations use typed event
kinds such as `rules_discovered`, `references_resolved`, `plan_created`,
`plan_approved`, `context_compacted`, `transaction_committed`,
`transaction_undo`, `verification`, `recovery_conflict` and `stream_error`.

v0.0.7 also records bounded `context_index`, `context_index_error` and
`hook_event` evidence. Provider capability metadata is diagnostic only; index
digests are revalidated before snippets are returned, and skills/hooks cannot
grant permissions.

The compatible exit-code baseline remains: `0` success/inspect-only success,
`1` execution/provider/verification or incomplete-audit failure, `2` invalid
input or unavailable resource, `3` recovery/hash/config conflict, and `130`
cooperative user cancellation.

## Acceptance record template

Each fresh scenario records command, exit code, run/plan/transaction ids,
changed paths, before/after/undo SHA-256, event sequence and verification
exit code.  Runtime records stay under ignored `.forgecode/` or `tmp/` and
are never staged.

The release acceptance uses deterministic offline cases for calculator, JSON,
interactive Plan/Act, compaction, resume/fork, hash conflicts, undo and broken
SSE. The final release-gate run covers 224 deterministic cases. Six symlink-alias
tests are platform-conditional and are skipped only when the current Windows
process lacks symlink creation permission; this Windows process reported all six
skips for that reason.
