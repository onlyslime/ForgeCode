# Versioning

ForgeCode uses semantic-looking versions in the form `vA.B.C` for every commit and release tag.

- A normal commit increments C by one.
- A requested B update increments B and resets C to zero.
- A requested A update increments A and resets B and C to zero.
- The owner must explicitly request an A or B update. The agent must not infer one from feature size.
- The version is recorded in `VERSION`, `pyproject.toml`, and `src/forgecode/__init__.py`.
- Commit subjects use `vA.B.C: short description`; release tags use the exact same version.

The initial framework commit is `v0.0.1: initialize framework`.
