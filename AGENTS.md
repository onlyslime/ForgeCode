# AGENTS.md

## Repository mission

ForgeCode is a self-built, auditable coding-agent framework for the graduate recommendation assessment. The repository must own the model protocol, conversation state, tool definitions, local execution, loop control, error handling, and safety boundary.

Do not add LangChain, LlamaIndex, OpenAI Agents SDK, Claude Agent SDK, AutoGen, CrewAI, or another ready-made agent framework/SDK. Vendor model API clients, OpenAI-compatible gateways, and native model tool calling are allowed. Do not wrap an existing agent product with a new UI and call that the implementation.

## Persistent goals

Each major phase of work is a persistent goal. When the user starts a goal, preserve the exact goal prompt in `docs/goals/YYYYMMDD-HHMMSS.md`, using the local creation time in Asia/Shanghai and a sortable timestamp. The `docs/goals/` directory is intentionally ignored by Git: goal prompts are private working records and must never be committed, pushed, or included in a release archive.

When a goal is continued, inspect its prompt and the current plan/state before doing work. Continue the same goal rather than silently restarting or replacing it. Keep working until the user explicitly says to stop, the goal is complete, or an external blocker requires the user's decision. Do not put API keys, tokens, or other secrets in a goal file.

Do not create a new goal prompt or goal file unless the owner explicitly asks
for one. A request to continue work means continue the active goal. Do not put
ordinary notes, status reports, research reports, or implementation plans in
`docs/goals/`; that directory contains timestamped goal prompts only.

## Version and commit policy

Every commit and release tag uses `vA.B.C`.

- A normal commit increments only C by one.
- Only an explicit user request may update A or B.
- An A update increments A and resets B and C to zero.
- A B update increments B and resets C to zero.
- Keep the same version in `VERSION`, `pyproject.toml`, and `src/forgecode/__init__.py`.
- Commit subjects use `vA.B.C: short description`; tags use the exact version.
- Before committing, run relevant tests and inspect `git status` for secrets and unintended files.

### Version history maintenance

- Maintain the tracked Markdown changelog at `docs/CHANGELOG.md`. Every
  versioned feature release must add a dated entry describing user-visible
  behavior, compatibility notes, and verification evidence; reconstruct older
  entries from the existing roadmap/acceptance files and git history when
  needed.
- Do not increment `C` for every small edit. A version change requires a
  substantive user-visible feature, protocol, security, persistence, or CLI
  capability. Documentation-only edits, tests, refactors, metadata syncs, and
  isolated bug fixes should be grouped under the current version and must not
  trigger a new release version.
- Keep the changelog and the version files synchronized whenever a substantive
  release is made; do not create duplicate entries for intermediate commits.

The current version is `v0.0.34`; the next ordinary versioned release is
`v0.0.35` unless the user explicitly requests an A or B update. Keep ordinary
fixes and documentation commits on the current version; increment C only for
substantive user-visible release content.

## Documentation boundaries

- `README.md` is public GitHub documentation. It must be understandable to ordinary readers and include both Chinese and English sections.
- `README.txt` is the assessment handoff. Keep it within 1000 Chinese characters and include the repository URL, run instructions, distinctive features, and only useful assessment notes.
- `docs/assignment/` stores assessment materials and is tracked.
- `docs/research/` stores the research plan and report and is tracked.
- `docs/goals/` stores ignored, timestamped goal prompts only.
- `docs/strategy/` stores local strategy/status notes and is intentionally
  ignored; never stage, push, or package its contents unless the owner changes
  this policy explicitly.

## Efficiency and test execution

Goals and tests must be useful, bounded, and evidence-driven. Do not turn a
small change into an unbounded repository-wide activity.

- Keep one goal focused on one coherent feature slice or release stage. State
  its scope, non-goals, completion conditions, stop conditions, affected files,
  and required checks before doing broad work. Do not expand into Pi P1/P2
  features while an assessment or release P0 blocker remains.
- Use impact-based test tiers. After a small change, run the new or modified
  tests plus directly affected existing tests and a quick compile/import check.
  After a coherent feature slice, run its related integration/CLI tests. Run
  the complete `uv run pytest -rs` suite at a milestone, after a shared-core
  change, before a release, or when the owner explicitly requests it.
- Do not rerun slow, unchanged integration, cross-process, recovery, or full
  CLI suites after every minor edit. In particular, existing tests that are
  unrelated to the changed files may wait for the next feature gate. This is a
  scheduling rule, not permission to weaken assertions or omit a required
  release gate.
- Use `git diff --name-only` and the module/test dependency map to select the
  smallest justified test set. Prefer targeted pytest paths, `-k`, and `--lf`
  for feedback; avoid parallel execution when tests share workspaces, locks,
  sessions, or transaction state.
- Documentation-only changes normally need link, formatting, and bounded-size
  checks rather than a full Python regression run. Changes to shared runtime
  code, security boundaries, persistence, provider protocol, or CLI contracts
  require their associated regression suites even when the patch is small.
- Record what was run, what was intentionally not run, the reason, duration,
  exit code, and any platform-conditional skip. Never describe a focused green
  run as a full regression pass. Refresh acceptance documents only from a
  fresh, explicitly identified gate run.
- Do not repeatedly reread unchanged documents or regenerate duplicate review
  artifacts. Start from the active goal, the latest state/evidence summary,
  `git diff`, and the affected modules; reread whole trees only when the
  change genuinely crosses their boundaries.
- Give every long-running command and goal a finite step/time budget. If there
  is no code, test, or state-event progress for a meaningful interval, stop
  extending the scope and diagnose provider latency, approval waiting, a
  blocked process, or redundant work. Waiting is not evidence of progress.
- Keep an auditable distinction between implementation time, test time,
  provider/network wait, human/approval wait, and repeated work. Prefer a
  short focused feedback loop and one full release gate over many identical
  full-suite runs.

## Engineering rules

- Use Python 3.11+ and `uv` for dependency and environment management unless the user explicitly changes the stack.
- Keep provider adapters, AgentLoop, ToolRegistry, WorkspaceGuard, SessionStore, and CLI separable and testable.
- Every filesystem operation must pass through workspace path validation. Side-effecting writes and commands require an explicit approval policy, timeout, and structured result.
- Preserve stdout, stderr, exit codes, tool errors, and model/tool metadata in logs or session events; do not hide failures.
- Prefer small standard-library implementations. Add dependencies only when they solve a demonstrated need.
- Use `apply_patch` for source and documentation edits. Never use destructive Git commands to discard user work.
- Run `uv run pytest`, `uv run forgecode doctor`, and relevant CLI checks before a release commit.
- Keep credentials in environment variables or ignored local files. Never print or commit real credentials.

## Push policy

- After a coherent feature slice (not each individual edit) is implemented and verified, commit the complete intended change set and push it to the configured upstream remote automatically. Group related code, tests, docs, and version updates into one feature commit. Use the required `vA.B.C: short description` subject and inspect `git status` before pushing.
- Future implementation updates in this repository are authorized to follow the same verified commit-and-push flow without asking again, unless the owner explicitly says to keep changes local.
