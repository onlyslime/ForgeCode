# Versioning

ForgeCode uses semantic-looking versions in the form `vA.B.C` for every commit and release tag.

- A normal commit increments C by one.
- A requested B update increments B and resets C to zero.
- A requested A update increments A and resets B and C to zero.
- The owner must explicitly request an A or B update. The agent must not infer one from feature size.
- The version is recorded in `VERSION`, `pyproject.toml`, and `src/forgecode/__init__.py`.
- Commit subjects use `vA.B.C: short description`; release tags use the exact same version.

The initial framework commit is `v0.0.1: initialize framework`.

The current framework version is `v0.7.61`. The ordinary next version after this
release is `v0.0.9`.

## v0.0.8 release checklist

Before the release commit, confirm that `VERSION`, `pyproject.toml` and
`src/forgecode/__init__.py` all contain `0.0.8`; run the focused and complete
test suites, `compileall`, `forgecode doctor`, and the documented CLI smoke
commands. Inspect `git status --short --ignored` for credentials, private
paths, generated runtime data and the ignored `docs/goals/` prompts. The
acceptance record in local ignored `docs/releases/v008-acceptance-report.md` must contain only bounded
command results, identifiers and digest summaries. Create exactly one commit
with subject `v0.0.8: harden cancellation, recovery, and evidence workflows`,
create annotated tag `v0.0.8`, and push the branch and tag only after local
checks pass. A network or permission failure must be reported as a blocker
rather than described as a successful publication.
