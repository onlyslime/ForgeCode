# Documentation Guide

This directory contains the project's reviewed, reproducible documentation.

## Reader guide

Most readers only need the root [`README.md`](../README.md), then
[`demo-script.md`](demo-script.md) for a short walkthrough. Use the following
documents when you need more detail:

- `architecture.md`, `durability-design.md`, `rpc-sdk.md`, and `privacy.md`:
  implementation and safety design (maintainer/technical review).
- `implemented-features.md` and `capability-trace.md`: capability inventory
  and the evidence behind each claim (assessment/review).
- `research/`: the research plan and report (project background).
- `VERSIONING.md` and `CHANGELOG.md`: release and history policy.

The material in `goals/`, `strategy/`, and `releases/` is intentionally local
working history. It may be useful to maintainers, but is not part of the
public reading path and must not be committed or packaged.

## Committed reference material

- `architecture.md`, `durability-design.md`, `rpc-sdk.md`, and `privacy.md`
  describe runtime design, persistence, embedding, and the security boundary.
- `capability-trace.md` maps user-visible capabilities to source, tests, and
  runnable evidence.
- `demo-script.md` is the short offline assessment walkthrough.
- `CHANGELOG.md` and `VERSIONING.md` define release conventions and history.
- `implemented-features.md` is the maintained list of claimed capabilities and
  its manual audit status.
- `research/` contains the research plan and report.

## Local-only material

`goals/` stores timestamped private goal prompts, while `strategy/` stores
local planning/status notes. Both directories are intentionally ignored by
Git. Runtime state such as `.forgecode/`, `sessions/`, logs, caches, temporary
workspaces, credentials, and `releases/` historical reports are also ignored;
do not copy them into tracked docs.

When adding documentation, prefer a stable topic path, avoid
absolute machine paths and secrets, and record only bounded, reproducible
verification evidence. Update links when moving a document and keep public
usage guidance in `README.md`/`README.txt` rather than in private notes.
