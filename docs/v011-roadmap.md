# ForgeCode v0.0.11 roadmap and evidence

This slice adds two Pi-inspired terminal shortcuts to the existing interactive
control surface. It keeps ForgeCode's single `AgentLoop`, `ToolRegistry`,
`WorkspaceGuard`, approval policy and durable session boundary.

## Delivered behaviour

| Shortcut | Behaviour | Safety/evidence |
|---|---|---|
| `!<command>` | Runs `run_command` through the production registry, then sends only a bounded, redacted result to one normal provider turn. | Act mode, risk classification, approval, timeout, cancellation and context revalidation are unchanged; the session stores a command fingerprint and bounded result. |
| `!!<command>` | Runs through the same registry and returns the bounded result only to the user/audit stream. | It never constructs or calls a provider turn; raw command text is not included in shortcut events. |

The input parser accepts only a literal line prefix, rejects empty, multiline,
ambiguous (`!!!`) and oversized commands, and leaves ordinary prose containing
`!` untouched. JSONL output remains one parseable envelope per line; prompts
and progress stay on stderr. Shortcut cancellation and pause use the same
interactive controller lifecycle and do not create a second worker or loop.

## Pi method mapping and limits

Pi's Bash injection and local/agent-visible command distinction motivated the
two prefixes. ForgeCode deliberately does not claim Pi compatibility: there
is no Escape driver, RPC/Node SDK, extension marketplace, subagent, worktree,
background scheduler, browser, cloud executor or OS sandbox. Shell commands
remain subject to the local risk classifier and explicit approval policy.

## Evidence

`tests/test_v011_command_shortcuts.py` covers deterministic parsing, Act/Plan
boundaries, provider suppression for `!!`, bounded provider context for `!`,
redaction, failure envelopes and cancellation. Directly affected v0.0.10
interactive/cancellation, shell, machine-contract and safety tests remain the
regression gate.

Fresh release-gate evidence (2026-08-28, Asia/Shanghai):

- `uv run pytest tests/test_v011_command_shortcuts.py tests/test_v010_interactive_controls.py tests/test_pause_and_legacy.py tests/test_cancellation_hardening.py tests/test_cli_machine_contract.py tests/test_security_and_tools_edges.py -q` — passed; 0 failed, 1 Windows symlink skip (about 20 seconds).
- `uv run pytest -rs` — exit 0; 352 passed, 8 Windows symlink skips, 2 existing collection warnings (163.46 seconds; wrapper elapsed 164.28 seconds).
- `uv run forgecode --workspace . doctor --jsonl` — exit 0 in 0.68 seconds; reported version `0.0.11` and status `ready` with no provider configured.
- `uv run python -m compileall -q src tests` — exit 0 in 0.33 seconds.
- `git diff --check` — exit 0. Documentation-only or unrelated historical suites were not run separately because the full gate covered the shared runtime; the final gate was rerun once after the output-bound and Plan-boundary fixes.
