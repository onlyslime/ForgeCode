# Contributing

ForgeCode owns its provider-neutral protocol, AgentLoop, tools, persistence,
CLI, and safety boundary. Do not add a ready-made agent framework or bypass
`WorkspaceGuard` for convenience.

Keep changes small and testable. Provider adapters belong under
`src/forgecode/models`; tools under `tools`; shared policy under `security` and
`config`; persistence under `storage`; command contracts under `application`.
Add deterministic tests for every new boundary and update the capability
trace when a user-visible claim changes.

Before a release, run compile, targeted tests, the full `uv run pytest -rs`
gate, `uv run forgecode doctor`, and the offline CLI walkthrough. Inspect
`git status` for secrets and ignored runtime files. Version files and
`docs/CHANGELOG.md` must stay synchronized; commit subjects use
`vA.B.C: short description`.

