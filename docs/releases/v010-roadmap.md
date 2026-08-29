# ForgeCode v0.0.10 roadmap and evidence

This release turns interactive chat from a synchronous wrapper into a bounded
control surface over the existing `AgentLoop`. It is Pi-inspired in terminal
workflow, but remains a ForgeCode-owned provider, tool, approval, persistence
and recovery implementation.

## Delivered in this slice

| Area | Behaviour | Evidence |
|---|---|---|
| Follow-up queue | One controller owns one FIFO worker and accepts bounded follow-ups. Item/character limits reject excess input and persist enqueue/dequeue/reject events. Cancellation clears pending follow-ups. | `tests/test_v010_interactive_controls.py` |
| Pause/resume | `/pause` is honoured at provider-return, tool-dispatch, approval, transaction and verification boundaries. Interactive mode keeps the same loop alive; `/resume` validates the session/checkpoint and rules/plan/config fingerprints before releasing it. | focused controls and cancellation tests |
| Cancellation | `/cancel`, EOF, `/quit` and Ctrl-C request cooperative cancellation and use bounded cleanup. Late provider responses are discarded before tool dispatch; an un-stoppable worker is recorded as unresolved/recovery-required. | cancellation/recovery regression |
| Model switching | `/model select` is rejected while a worker or queued request is active; idle selection records old/new profile and capabilities without exposing credentials. | CLI machine-contract and profile regression |
| Machine interface | `chat --jsonl` emits parseable envelopes on stdout; approval/progress diagnostics stay on stderr. Existing `--json` and `InteractiveSession` positional APIs remain compatible. | `test_cli_machine_contract.py`, v0.0.9 regressions |

## Pi method mapping

Pi's steering/follow-up and interrupt ideas informed the controller and command
surface. Pi's interactive/print/JSON/RPC distinction informed the strict JSONL
envelope. ForgeCode deliberately keeps one Python AgentLoop, explicit approval,
WorkspaceGuard, transaction/checkpoint evidence and fail-closed cancellation;
Pi is a design reference, not a dependency.

## Boundaries

No Escape-specific terminal driver, second agent loop, parallel worker, cloud
execution, MCP marketplace, browser/IDE integration, worktree, background
scheduler or OS sandbox claim is made. Provider cancellation remains
cooperative; a detached provider is never treated as successful and requires
recovery evidence. Windows interactive control is exposed through tested slash
commands and input streams rather than an unreliable Escape promise.

## Verification record

The release record must be refreshed from a fresh gate run. Focused control,
interactive, provider-cancellation, recovery and machine-contract suites are
the per-edit gate; the complete `uv run pytest -rs`, compile/import smoke,
`uv run forgecode doctor` and a temporary-workspace chat demonstration are the
milestone gate. Record command, duration, exit code, skips/warnings and any
intentionally deferred unrelated suites in the v0.0.10 acceptance report.
