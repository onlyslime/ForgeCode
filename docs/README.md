# Documentation Guide

This directory contains the project's reviewed, reproducible documentation.

## Committed reference material

- `architecture.md`, `durability-design.md`, `rpc-sdk.md`, and `privacy.md`
  describe runtime design, persistence, embedding, and the security boundary.
- `capability-trace.md` maps user-visible capabilities to source, tests, and
  runnable evidence.
- `demo-script.md` is the short offline assessment walkthrough.
- `VERSIONING.md` and `CHANGELOG.md` define release conventions and history.
- `releases/` contains versioned roadmaps and acceptance/release reports.
- `research/` contains the research plan and report; `assignment/` contains
  the supplied assessment material.

## Local-only material

`goals/` stores timestamped private goal prompts, while `strategy/` stores
local planning/status notes. Both directories are intentionally ignored by
Git. Runtime state such as `.forgecode/`, `sessions/`, logs, caches, temporary
workspaces, and credentials is also ignored; do not copy it into tracked docs.

When adding documentation, prefer a stable topic or release path, avoid
absolute machine paths and secrets, and record only bounded, reproducible
verification evidence. Update links when moving a document and keep public
usage guidance in `README.md`/`README.txt` rather than in private notes.
