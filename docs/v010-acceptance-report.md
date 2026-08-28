# ForgeCode v0.0.10 acceptance report

Date: 2026-08-28 (Asia/Shanghai). This report records the fresh local gate for
the interactive runtime control slice; ignored runtime files are excluded.

## Gate

| Command | Exit | Result |
|---|---:|---|
| `uv run pytest tests/test_v010_interactive_controls.py -q` | 0 | 4 focused control/queue/pause/model-switch tests |
| `uv run pytest tests/test_v010_interactive_controls.py tests/test_pause_and_legacy.py tests/test_v009_features.py tests/test_v006_transaction_recovery_interactive.py tests/test_v006_cli_workflows.py tests/test_cli_machine_contract.py tests/test_cancellation_hardening.py tests/test_runtime_audit.py -q` | 0 | affected regression set passed |
| `uv run python -m compileall -q src tests` | 0 | source/tests compile smoke |
| `uv run forgecode --workspace . doctor --jsonl` | 0 | ready; provider unconfigured; no network request |
| `uv run pytest -rs` | 0 | **344 passed, 8 skipped, 2 warnings** in 149.98s (fresh final gate) |
| `uv run python -m compileall -q src tests` | 0 | source/tests compile smoke (0.35s) |
| `uv run forgecode --workspace . doctor --jsonl` | 0 | ready; provider unconfigured; no network request (0.62s) |
| `chat --demo --auto-approve --jsonl` in a temporary workspace | 0 | 1 stdout envelope, 0 malformed JSONL lines, 0 stderr bytes (0.79s) |

The eight skips are Windows symlink/junction permission conditions explicitly
reported by the tests. The two warnings are existing pytest collection
warnings for helper classes with constructors. No assertion was weakened.
The compile, doctor and temporary-workspace JSONL smoke checks were run after
the final source edit; the demo used an ignored temporary directory and made
no network request.

## Feature evidence

`InteractiveRunController` drains one FIFO worker with bounded queue limits,
persists control events, and drops follow-ups after cancellation. The same
`AgentLoop` now pauses at provider/tool/approval/verification boundaries and
resumes only after session/checkpoint plus rules/plan/config fingerprint
validation. Late provider responses are discarded before tool dispatch; an
unresolved worker is surfaced as recovery-required rather than success.

The chat CLI exposes `/pause`, `/resume`, `/cancel`, and active model-switch
rejection. `chat --jsonl` emits parseable envelopes on stdout while diagnostics
remain on stderr. Existing `InteractiveSession`, `--json`, v0.0.9 features,
transaction, approval, workspace and provider-cancellation regressions remain
covered by the affected set and full gate above.

## Delivery boundary

No Escape-specific terminal driver, second agent loop, parallel worker, cloud
execution, MCP marketplace, browser/IDE integration, worktree or OS sandbox
claim is made. The configured GitHub remote remains private; changing
visibility is an owner decision required by the assessment.
