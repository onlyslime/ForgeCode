# ForgeCode v0.0.8 roadmap and evidence contract

This roadmap records the gap analysis after the v0.0.7 release and the
completion contract for v0.0.8. The implementation makes test execution,
review, recovery and diagnostics first-class while retaining the self-built,
local and auditable boundary. Items below describe shipped code; the exact
fresh-workspace evidence and final test count are kept in
`docs/v008-acceptance-report.md`.

## Baseline and gaps

| Area | v0.0.7 evidence | v0.0.8 implementation | Acceptance evidence |
|---|---|---|---|
| Workspace/path safety (F01/F14) | `security/workspace.py`, tool tests | Profiles, reports and extension caches reuse path/alias/race guards | escape, alias, junction and race tests |
| Files/search/context (F02-F04/F15) | `context/index.py`, `context search` | Exclusion explanations, line/symbol/language filters and stale-read diagnostics | fresh multilingual index and concurrent-edit tests |
| Editing/transactions (F05-F07/F18-F19) | patch/write tools and durable ledger | Review joins hash-linked transaction hunks; CAS/locks protect writers | successful, partial, conflict and undo scenarios |
| Commands/testing (F08-F10) | `ShellTool`, verifier and `/test` | Strict TOML argv profiles, setup/teardown, env allow-list, quotas and `test` CLI | profile list/show/run, timeout, environment and JSONL tests |
| Provider/loop (F11-F12) | OpenAI-compatible SSE and `AgentLoop` | Cancellation token, deadline propagation, bounded worker cleanup and typed retry attempts | fake transport faults and no post-side-effect replay |
| Approval/plan/rules (F13/F17/F21) | Plan/Act registry gate, `AGENTS.md` resolver | Profile/security decisions are recorded in review; rules/references/config fingerprints still gate Plan → Act | plan refusal, stale handoff and approval tests |
| Skills/hooks (F22/F28) | strict manifest, loader and lifecycle events | Deterministic precedence/migration/state and correlation/cleanup evidence | executable approval, hook policy and cleanup tests |
| Sessions/export (F16/F28) | JSONL/checkpoint/recovery and redaction | Cross-process sequence/CAS, interrupted pending-action recovery and strict envelopes | resume/fork/compact and mixed-stream tests |
| Review/security (new) | transaction review and session aggregation | Stable report, four deterministic checks, artifact export/import/verify | pass/fail/skipped/error findings and tamper rejection |
| P2 market features (F23-F27) | explicitly not implemented | Remain explicit product boundaries; no unsafe shallow substitutes | README, architecture and report state the limits |

## Diagnostic and machine-output contract

Every new machine-facing command returns bounded UTF-8 JSON. The top-level
object contains `schema_version` (positive integer), `kind`, `ok`, a stable
`command`, and either a `data` object or an `error` object. `error` contains a
short `code`, safe `message`, and optional bounded `details`; secrets, absolute
private paths and raw backups are redacted. Numbers must be finite and strings,
arrays and objects have explicit size limits. JSONL emits one such object per
line on stdout; progress, approval prompts and diagnostics are stderr-only.

Event records remain append-only JSONL envelopes. New event payload fields are
additive and guarded by the envelope `schema_version`; readers accept older
versions and reject unknown dangerous shapes. A report references events and
files by sequence and SHA-256 digest instead of embedding raw session or backup
content. Report verification recomputes the digest against the current
workspace and returns `stale` or `tampered` rather than a success claim.

## Delivery order and completion state

1. Test profiles and shared verification evidence — implemented.
2. Evidence-driven review and built-in security checks — implemented.
3. Cancellation/deadline/retry and interrupted-session recovery — implemented.
4. Skills/hooks/context ergonomics and bounded property tests — implemented.
5. CLI integration and documentation — implemented; final release gate is
   recorded in the acceptance report immediately before the v0.0.8 commit/tag.

The release version is `v0.0.8`; A/B components remain unchanged. Future work
must use the repository version policy and must not silently promote the P2
boundaries into claims of support.
