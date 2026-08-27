# AGENTS.md

## Repository mission

ForgeCode is a self-built, auditable coding-agent framework for the graduate recommendation assessment. The repository must own the model protocol, conversation state, tool definitions, local execution, loop control, error handling, and safety boundary.

Do not add LangChain, LlamaIndex, OpenAI Agents SDK, Claude Agent SDK, AutoGen, CrewAI, or another ready-made agent framework/SDK. Vendor model API clients, OpenAI-compatible gateways, and native model tool calling are allowed. Do not wrap an existing agent product with a new UI and call that the implementation.

## Persistent goals

Each major phase of work is a persistent goal. When the user starts a goal, preserve the exact goal prompt in `docs/goals/YYYYMMDD-HHMMSS.md`, using the local creation time in Asia/Shanghai and a sortable timestamp. The `docs/goals/` directory is intentionally ignored by Git: goal prompts are private working records and must never be committed, pushed, or included in a release archive.

When a goal is continued, inspect its prompt and the current plan/state before doing work. Continue the same goal rather than silently restarting or replacing it. Keep working until the user explicitly says to stop, the goal is complete, or an external blocker requires the user's decision. Do not put API keys, tokens, or other secrets in a goal file.

## Version and commit policy

Every commit and release tag uses `vA.B.C`.

- A normal commit increments only C by one.
- Only an explicit user request may update A or B.
- An A update increments A and resets B and C to zero.
- A B update increments B and resets C to zero.
- Keep the same version in `VERSION`, `pyproject.toml`, and `src/forgecode/__init__.py`.
- Commit subjects use `vA.B.C: short description`; tags use the exact version.
- Before committing, run relevant tests and inspect `git status` for secrets and unintended files.

The current version is `v0.0.6`; the next ordinary commit is `v0.0.7` unless the user explicitly requests an A or B update.

## Documentation boundaries

- `README.md` is public GitHub documentation. It must be understandable to ordinary readers and include both Chinese and English sections.
- `README.txt` is the assessment handoff. Keep it within 1000 Chinese characters and include the repository URL, run instructions, distinctive features, and only useful assessment notes.
- `docs/assignment/` stores assessment materials and is tracked.
- `docs/research/` stores the research plan and report and is tracked.
- `docs/goals/` stores ignored, timestamped goal prompts only.

## Engineering rules

- Use Python 3.11+ and `uv` for dependency and environment management unless the user explicitly changes the stack.
- Keep provider adapters, AgentLoop, ToolRegistry, WorkspaceGuard, SessionStore, and CLI separable and testable.
- Every filesystem operation must pass through workspace path validation. Side-effecting writes and commands require an explicit approval policy, timeout, and structured result.
- Preserve stdout, stderr, exit codes, tool errors, and model/tool metadata in logs or session events; do not hide failures.
- Prefer small standard-library implementations. Add dependencies only when they solve a demonstrated need.
- Use `apply_patch` for source and documentation edits. Never use destructive Git commands to discard user work.
- Run `uv run pytest`, `uv run forgecode doctor`, and relevant CLI checks before a release commit.
- Keep credentials in environment variables or ignored local files. Never print or commit real credentials.
