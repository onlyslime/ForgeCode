# Upstream Agent Comparison

This note records a source-based comparison for the reliability iteration
goal. It is an audit artifact, not a dependency proposal; no upstream code is
copied into ForgeCode.

## Sources

| Project | Source | Commit inspected | License |
| --- | --- | --- | --- |
| OpenAI Codex CLI | https://github.com/openai/codex | `0a12b855a0b21068108a8a3b311d492712737e0f` | Apache-2.0 |
| OpenCode | https://github.com/anomalyco/opencode | `10765ff2a9da8c3b88e4de873aa383a49c318912` | MIT |

Codex was inspected through Git tree objects because several Windows snapshot
paths exceed the local checkout limit. OpenCode was shallow-cloned normally.

The latest reachable heads were rechecked on 2026-08-30: Codex advanced to
`88f776588f5e73467e7659c268f8358a9a2378b6`; OpenCode remains at the audited
`10765ff2a9da8c3b88e4de873aa383a49c318912` commit.

## Capability matrix

| Area | ForgeCode | Codex CLI evidence | OpenCode evidence | Decision |
| --- | --- | --- | --- | --- |
| Model/tool protocol | Provider-neutral normalized calls | Rust app-server schemas include dynamic tools and command/file approvals | Typed provider and tool packages | Keep ForgeCode's provider-neutral contract; add tests for malformed fragments only. |
| Safety boundary | WorkspaceGuard, approval scopes, bounded commands | App-server approval request/response schemas and sandbox-oriented components | Permission rulesets, external-directory checks | Preserve local explicit approvals; do not copy a sandbox assumption. |
| Context | Bounded index, search, compaction | Large app-server protocol and context-aware TUI modules | Dedicated compaction prompt and context/session services | Improve evidence around compaction, not prompt complexity. |
| Sessions/recovery | Durable JSONL sessions, checkpoints, transaction undo | Agent graph store and extensive session protocol schemas | ACP resumeSession and persistent server sessions | Add recovery matrix tests where a process ends mid-transaction. |
| Background/concurrency | Bounded background process tools | Server/exec protocol supports streaming and termination | `background/job` service and session event streams | Keep bounded ownership and termination semantics; avoid unbounded jobs. |
| Interfaces | REPL, JSON/JSONL, RPC, Python/Node SDK | App-server JSON-RPC protocol | HTTP/ACP/SDK packages | Verify existing machine contracts before adding endpoints. |
| Extensions | Validated skills/hooks with quotas | Skills and app-server extension surfaces | Plugins, MCP, commands and subagents | Do not add MCP/subagents for the assessment; document the boundary. |
| Verification | Named tests, review report, hashes | Command execution and approval protocol evidence | Session review UI and tool error states | Strengthen failure-to-verification evidence in offline runs. |

## Iteration outcome

The proposed high-value checks were mapped to existing coverage before making
changes. `test_lifecycle_durable.py` and `test_rpc_session_lifecycle.py` cover
partial streams, sequence gaps, mixed identities, terminal-state forgery and
resume events. `test_v006_config_stream.py` and `test_cancellation_hardening.py`
cover truncated JSON, duplicate frames, out-of-order tool indexes, cancellation
and response limits. The offline walkthrough already exports and verifies a
hash-bound review artifact. A second audit therefore found no high-confidence
eligible gap that justifies new runtime behavior; adding the excluded upstream
features would increase risk without improving the assessment submission.

Features intentionally excluded are remote execution, MCP, subagents, broad
plugin loading, and OS-level sandbox claims: they either violate the assessment
boundary, require a product decision, or cannot be safely validated here.
