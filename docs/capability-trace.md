# ForgeCode v0.0.9 capability trace

This document is the implementation trace for the v0.0.8 release (building on
v0.0.7). It is intentionally evidence-oriented: a capability is considered
complete only when its source, tests, and a runnable CLI/fresh-workspace
scenario agree. Runtime records remain bounded and are not committed.

## Scope mapping

| Capability family | Earlier baseline | v0.0.8 target/source | Evidence status |
| --- | --- | --- | --- |
| Provider-neutral loop, tool schemas, Plan/Act gate | `agent/loop.py`, `tools/base.py` | Preserve rule/reference/plan context and add cancellation checks before dispatch | regression + `test_cancellation_hardening.py` |
| Workspace-safe read/write/patch/command | `security/workspace.py`, `tools/*` | Apply the same path/approval boundary to profiles, reports and extension caches | workspace, patch and race tests |
| Session JSONL/checkpoint/recovery | `storage/session.py`, `storage/checkpoint.py` | Cross-process locks/CAS, pending-action and unresolved recovery evidence | lifecycle + recovery tests |
| Scoped rules, references and structured plans | `rules.py`, `references.py`, `plan.py` | Preserve fingerprints and stale handoff checks | rules/reference/plan regression |
| Interactive session | interactive service/REPL | Keep `/plan`, `/test`, `/review`, `/compact`, `/undo` and extension commands on shared services | interactive regression |
| Durable transaction/undo | persistent ledger baseline | Hash-checked partial undo, external-edit protection and review linkage | transaction/recovery tests |
| Typed config/profile/policy | TOML config baseline | Strict named `.forgecode/tests.toml` argv profiles with quotas and expected exits | `tests/test_test_profiles.py`, CLI profile tests |
| Verification and test evidence | bounded shell verifier | `TestProfileRunner` setup/main/teardown, cancellation and digest-bounded `test_profile_result` | profile + cancellation tests |
| Streaming and provider resilience | bounded Chat Completions/SSE | Deadline/cancellation propagation, retry attempts and unresolved worker records | provider/SSE hardening tests |
| Incremental context index/search | repository map and v0.0.7 index | Symbol extraction, line/language/glob filters, exclusion explanations and stale diagnostics | `test_context_extensions_deep.py`, hardening tests |
| Skills and extension manifest | strict Markdown/manifest loader | Precedence, schema migration, state enable/disable/remove/restore and executable boundaries | extension deep/hardening tests |
| Lifecycle hooks | approval observers | Correlation ids, timeout/cleanup history and fail-closed recovery evidence | hook extension tests |
| Evidence-driven review/security | transaction review baseline | Stable report joining session/plan/context/transaction/test/hook/diff plus four deterministic checks and signed artifacts | `review.py`, review/CLI tests |
| CLI machine contract | mixed legacy JSON output | Strict JSONL envelope with mutually exclusive `data`/`error`, stderr diagnostics and exit-code mapping | `test_cli_machine_contract.py` |
| F23-F27 research extensions | intentionally out of scope | IDE/autocomplete, browser/computer control, cloud/worktrees, multi-agent/background orchestration, enterprise governance | explicit post-release boundary |
| Rolling context and trajectory | new v0.0.9 slice | automatic serialized-budget compaction, source fingerprints, holistic event scoring and bounded repair evidence | `agent/loop.py`, `evaluation.py`, v0.0.9 feature tests |
| Profiles, session tree and completion | new v0.0.9 slice | validated profile catalog/switch audit, non-replaying clone/import/tree, advisory path suggestions | config/CLI/context/session services and v0.0.9 feature tests |

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

v0.0.8 additionally records `test_profile_result`, `provider_attempt`,
`provider_retry`, `review` and bounded recovery/unresolved evidence. Provider
capability metadata is diagnostic only; index digests are revalidated before
snippets are returned, profiles cannot pass after cancellation/timeout, and
skills/hooks cannot grant permissions.

The compatible exit-code baseline remains: `0` success/inspect-only success,
`1` execution/provider/verification or incomplete-audit failure, `2` invalid
input or unavailable resource, `3` recovery/hash/config conflict, and `130`
cooperative user cancellation.

## Acceptance record template

Each fresh scenario records a bounded command (or profile name), exit code,
run/plan/transaction/report ids, changed paths, before/after/undo SHA-256,
event sequence, check statuses, and verification exit code. Runtime records
stay under ignored `.forgecode/` or `tmp/` and are never staged; acceptance
notes contain no private absolute paths, credentials, goal prompts, raw session
lines or backup bytes.

The release acceptance uses deterministic offline cases for calculator, JSON,
interactive Plan/Act, named test profiles, compaction, resume/fork, hash
conflicts, undo, cancellation, unresolved providers, review export/verify and
broken SSE. The release-gate count is recorded in
`docs/releases/v008-acceptance-report.md` after the final test run. Symlink-alias tests
are platform-conditional and are skipped only when the current Windows process
lacks symlink creation permission; the report records the exact skip reason.
