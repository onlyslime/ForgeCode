# CLI reference (v1.0.0)

`forgecode` and its short alias `fcc` share one parser. Human output is the
default; add `--json` for one bounded JSON value or `--jsonl` for a stream.
Commands that can mutate a workspace require Act mode, trust, and the
configured approval policy.

## Global options

`--workspace PATH` selects an existing workspace, `--mode plan|act|bypass`
selects the execution boundary, `--offline` disables provider networking, and
`--json`/`--jsonl` select machine output. `--help` is available on every
command. Bypass and `--auto-approve` are intended only for disposable trusted
workspaces.

## Command groups

| Command | Purpose |
|---|---|
| `chat` / `run` | Interactive or one-shot agent execution. |
| `plan` | Produce a structured read-only plan. |
| `inspect`, `status`, `diff` | Read-only workspace and latest-run views. |
| `session show|export|inspect|compact|fork|tree|clone|import` | Durable session evidence and branches. |
| `context index|search|complete|show|clear|explain|diagnostics` | Build and query the bounded context index. |
| `rules show|check|explain`, `memory add|show|remove|clear` | Manage untrusted project context. |
| `skills list|check|show|run` | Discover and invoke validated extensions. |
| `config show|validate|profiles|policy` | Inspect effective configuration without secrets. |
| `provider list|health`, `login`, `trust` | Provider diagnostics, credential hints, and workspace trust. |
| `test list|show|run`, `eval` | Named verification profiles and trajectory scoring. |
| `review`, `transaction` | Evidence reports and safe undo/recovery. |
| `telemetry status|export`, `tools`, `doctor`, `rpc` | Audit, schemas, health, and JSONL service mode. |

## Exit codes and errors

Zero means the requested operation completed. Non-zero results preserve a
bounded error code/message in machine envelopes; common codes include
`invalid_params`, `approval_denied`, `trust_revoked`, `timeout`, `cancelled`,
`transaction_conflict`, and `recovery_required`. Never parse human text when
`--jsonl` is available.

## Minimal examples

```powershell
uv run forgecode doctor --json
uv run forgecode --workspace $pwd plan "add a regression test"
uv run forgecode --workspace $pwd run --demo --auto-approve
uv run forgecode --workspace $pwd session tree --jsonl
uv run forgecode --workspace $pwd review --json
```
