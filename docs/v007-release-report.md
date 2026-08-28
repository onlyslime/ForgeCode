# ForgeCode v0.0.7 release acceptance

Date: 2026-08-28 (Asia/Shanghai)

This report records the final offline acceptance of the v0.0.7 implementation.
All scenarios used freshly generated temporary workspaces; runtime files remain
ignored under each workspace's `.forgecode/` directory.

## Automated gate

The final `uv run pytest -rs` completed with **224 passed, 6 skipped** in 135.59 seconds.
The six skips are the existing symlink-alias cases; the Windows process used
for this release does not have permission to create symlinks. `compileall`,
`doctor`, version/help, tools, rules JSON, context/skills help, config
validation, and offline provider health all exited successfully. The JSONL
demo emitted 87 parseable stdout lines, with zero stderr bytes.

## Fresh I-M acceptance

| Scenario | Evidence |
|---|---|
| I — skills | A Markdown read-only skill listed, showed and ran successfully. A Python/write skill was listed but returned `approval_required` without approval. Executable skills receive a filtered environment and cannot grant extra workspace access. |
| J — context | Fresh indexes covered text, Unicode and multiple file types; `.env`, binary, logs, JSONL sessions and backups were excluded. Incremental edit/delete and corrupted-index rebuild were exercised by the extension suite. Search results are deterministic, bounded and digest checked. |
| K — provider | Fake transports covered normal/fallback/required streaming, malformed JSON/SSE, retryable status and transport timeout. The final suite confirms no tool or transaction is replayed after a side effect. `provider health --json` performs no network request. |
| L — durability | The cross-process tests appended one ordered session stream, then inspected/exported/forked it and exercised review/undo. Headless `run --jsonl` remained line-parseable with diagnostics off stdout. |
| M — integrated workflow | The fresh offline calculator run traversed rules/context index → Plan/Act → approved patch → real failing-then-passing pytest → review/transaction evidence. A separate run with an external edit before undo returned exit code 3 and preserved the edit. Plan mode completed without invoking side-effecting tools. |

The final release run also verified that transaction manifests contain exact
before/after hashes, rollback availability, verification output and conflict
diagnostics; model prose cannot mark a review passed.

## Deliberate limits

F23–F27 remain documented post-release boundaries: no remote MCP execution,
browser/computer control, cloud runner, worktrees, parallel subagents or
enterprise governance. The command classifier and filesystem checks are
defense-in-depth controls, not an operating-system sandbox.
