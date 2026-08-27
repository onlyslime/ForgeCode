# ForgeCode

ForgeCode is a self-built coding-agent framework for the graduate recommendation assessment. It is intentionally independent of agent orchestration SDKs: the project owns the model protocol, tool registry, local execution, session history, loop control, and safety checks.

## Status

Version `v0.0.1` is the framework skeleton. It includes a runnable CLI, a workspace guard, file/search/shell tool interfaces, JSONL session storage, and tests. Model-provider adapters and the full task loop are deliberately kept behind interfaces for the next increment.

## Development

```powershell
uv sync
uv run forgecode doctor
uv run pytest
```

Run `uv run forgecode tools` to inspect the built-in tool definitions. API keys belong in environment variables or an ignored local `.env` file; never commit credentials.

## Layout

```text
src/forgecode/       framework code
tests/               automated tests
docs/assignment/     assessment materials
docs/research/       research plan and report
```

## Version policy

Every commit and tag uses `vA.B.C`. Until the owner explicitly requests an A or B update, only C increments. An A update resets B and C to zero; a B update resets C to zero.
