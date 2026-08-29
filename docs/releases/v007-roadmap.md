# ForgeCode v0.0.7 capability roadmap

This document is the traceability baseline for the v0.0.7 goal.  The atomic
capabilities and priorities come from `docs/research/deep-research-report.md`.
Evidence names the current production entry point; new v0.0.7 evidence is added
as implementation and acceptance tests land.

| ID | Capability / priority | Current evidence | v0.0.7 disposition |
|---|---|---|---|
| F01 | Workspace root / path boundary (P0) | `security/workspace.py`, workspace tests | Preserve; add index/skill path tests |
| F02 | File list and glob (P0) | `tools/filesystem.py`, `test_tools.py` | Preserve; expose through indexed context |
| F03 | Text/regex search (P0) | `SearchTool`, repository map tests | Extend with `context search` |
| F04 | Bounded file read (P0) | `ReadFileTool`, edge tests | Preserve and revalidate indexed digest |
| F05 | Exact edit (P0) | `WriteFileTool` conflict check | Preserve; skill permissions cannot bypass |
| F06 | Unified patch (P0) | `ApplyPatchTool`, patch regression suite | Preserve; review evidence remains transactional |
| F07 | Create files/directories (P0) | `WriteFileTool`, transaction ledger | Preserve; explicit approval required |
| F08 | Shell execution (P0) | `ShellTool`, command-bound tests | Preserve; profile/verifier shares same path |
| F09 | Test/lint command (P0) | run service and interactive `/test` | Add test profiles and evidence contract |
| F10 | Error context feedback (P0) | AgentLoop tool messages | Add bounded indexed/review context |
| F11 | Tool-call parsing (P0) | `models/openai_compatible.py`, provider tests | Extend health/protocol diagnostics |
| F12 | Loop termination/retry (P0) | `agent/loop.py`, lifecycle tests | Add cancellation and no-side-effect retry tests |
| F13 | Approval policy (P0) | `ToolContext`, approval and CLI tests | Apply to skill manifests and hooks |
| F14 | Path sandbox (P0) | `WorkspaceGuard`, alias/escape tests | Apply to index, skill and extension cwd |
| F15 | Output/context budget (P0) | `ContextBuilder`, bounded storage | Add indexed source selection and reasons |
| F16 | Summary/compact/recovery (P1) | `agent/recovery.py`, session CLI tests | Preserve; add export/import diagnostics |
| F17 | Plan/Act state machine (P1) | `plan.py`, lifecycle and plan tests | Skills and hooks fail closed in Plan |
| F18 | Diff display (P0) | `diff`, transaction review | Add review severity/source ordering |
| F19 | Checkpoint/rollback (P1) | `storage/checkpoint.py`, transaction tests | Preserve; cross-process recovery tests |
| F20 | Git read-only context (P1) | `references.py`, repository map | Keep commit/push out of default tools |
| F21 | Project rules files (P1) | `rules.py`, rules CLI/tests | Include rule fingerprint in index/context audit |
| F22 | Slash command / Skill (P1) | interactive slash dispatcher | v0.0.7: manifest, loader, registry, `/skills` CLI |
| F23 | MCP / external tools (P2) | ToolRegistry is provider-neutral | Route reserved; no remote execution this release |
| F24 | Browser / screenshots (P2) | Not implemented by design | Remain explicitly post-release |
| F25 | Subagents (P2) | Not implemented by design | Remain explicitly post-release |
| F26 | Worktree / parallelism (P2) | Not implemented by design | Remain explicitly post-release |
| F27 | CI/PR/cloud delivery (P2) | Headless CLI only | Remain explicitly post-release |
| F28 | Logging / observability (P0) | Session events, retry and verification metadata | Add health metrics, bounded diagnostics and audit assertions |

## v0.0.7 invariants

- A skill or manifest is untrusted data.  Loading it can register metadata and
  prompt content, but cannot grant write, shell, network or secret access.
- Index files are cache artifacts under `.forgecode`; they are atomically
  replaced, digest checked before snippets are returned, and safe to rebuild.
- Provider retry is allowed only before local side effects.  A tool result or
  transaction makes replay unsafe and must stop with an explicit diagnostic.
- Plan mode filters side-effecting schemas and the registry repeats the check;
  hooks cannot recurse into the agent or alter approval decisions.
- Human output may be verbose, but JSON/JSONL stdout is a stable payload only;
  progress, approval and diagnostics go to stderr.
- P2 features remain documented limitations rather than shallow or unsafe
  implementations.

## Evidence required before the v0.0.7 release

The release gate must include targeted extension/index tests, the complete
`uv run pytest -rs` result with real skip reasons, CLI smoke for `skills` and
`context`, fake-provider fault injection, cross-process session/transaction
checks, fresh I-M acceptance scenarios, a staged secret scan, and a clean
installation/version check.  The final release is one `v0.0.7` commit and tag;
goal prompts, runtime data and temporary workspaces remain ignored.
