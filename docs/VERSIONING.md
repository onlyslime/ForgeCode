# Versioning

ForgeCode uses semantic-looking versions in the form `vA.B.C` for every commit and release tag.

- A normal commit increments C by one.
- A requested B update increments B and resets C to zero.
- A requested A update increments A and resets B and C to zero.
- The owner must explicitly request an A or B update. The agent must not infer one from feature size.
- The version is recorded in `VERSION`, `pyproject.toml`, and `src/forgecode/__init__.py`.
- Commit subjects use `vA.B.C: short description`; release tags use the exact same version.

The initial framework commit is `v0.0.1: initialize framework`.

The current framework version is maintained in the repository's version files;
the next version is determined by the requested release level and the rules
above. This document intentionally avoids hard-coding a release number so that
the checklist remains valid as the project evolves.

## Release checklist

Before a release commit, confirm that `VERSION`, `pyproject.toml` and
`src/forgecode/__init__.py` contain the same version. Run the focused and
complete test suites, `compileall`, `forgecode doctor`, and the documented CLI
smoke commands. Inspect `git status --short --ignored` for credentials, private
paths, generated runtime data, and ignored `docs/goals/` prompts. Update
`docs/CHANGELOG.md` with the user-visible behavior and verification evidence.
Create one commit with subject `vA.B.C: short description`, create the matching
annotated tag when a release is intended, and push the branch and tag only
after local checks pass. Report network or permission failures as blockers
rather than describing publication as successful.
