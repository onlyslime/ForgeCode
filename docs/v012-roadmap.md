# ForgeCode v0.0.12 roadmap and evidence

This slice implements the Pi-inspired runtime tool allowlist without adding a
second execution loop or an external agent SDK.

## Delivered scope

- `chat`, `start`, and `run` accept bounded `--tools a,b`,
  `--exclude-tools a,b`, and `--no-tools` options.
- CLI policy is applied after the configured `tool_policy`, so it can only
  narrow permissions. Empty, duplicate, unknown, overlapping, or contradictory
  selections fail with `tool_policy_invalid`.
- The filtered registry is shared by provider schemas, AgentLoop tool calls,
  verification, interactive `/test`, and `!`/`!!` shortcuts. A filtered tool
  returns `tool_unavailable` without starting a process or provider turn.
- `--no-tools` produces an empty provider schema while preserving natural
  language turns. Policy decisions are recorded as bounded `tool_policy`
  session events without credentials or command text.

Pi features intentionally deferred: multiple providers/login, RPC/Node SDK,
Escape key handling, project trust/telemetry, and TypeScript extensions.

## Evidence

Focused: `uv run pytest tests/test_v012_tool_policy.py -q` passed (9 tests),
then `tests/test_v011_command_shortcuts.py tests/test_v006_config_stream.py
tests/test_agent_edges.py tests/test_security_and_tools_edges.py -q` passed
with one existing Windows symlink skip. `uv run python -m compileall -q src
tests` passed.

Fresh release evidence (2026-08-28, Asia/Shanghai):

- `uv run pytest -rs` — exit 0; 361 passed, 8 Windows symlink skips, 2 existing collection warnings (195.61 seconds; wrapper elapsed 196.50 seconds).
- `uv run forgecode --workspace . doctor --jsonl` — exit 0 in 0.66 seconds; version `0.0.12`, status `ready`, provider unconfigured.
- `uv run python -m compileall -q src tests` — exit 0 in 0.33 seconds.
- `git diff --check` — exit 0. No provider-network tests were run; the deterministic provider fakes cover schema and no-tools behavior.
