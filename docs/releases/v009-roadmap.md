# ForgeCode v0.0.9 roadmap and evidence

This release keeps the v0.0.8 local, provider-neutral safety boundary and
adds productisation for long-running, auditable work.

## Delivered

| Area | Implementation | Evidence |
|---|---|---|
| Rolling context | `AgentLoop` measures serialized context, automatically appends a bounded `context_compacted` event, retains safety/intent and recent tool pairings, and records source sequence plus summary fingerprint. | `tests/test_v009_features.py::test_auto_compaction_preserves_goal_pairing_and_is_bounded` |
| Holistic trajectory evaluation | `forgecode eval`/`benchmark` scores durable events (completion, verification, failures, repairs, approvals, compaction, conflicts, cancellation and audit state). Model prose never overrides evidence. | `evaluation.py`, evaluator regression |
| Safe path completion | `context complete` and interactive `/files [prefix]` return stable relative suggestions with exclusion reasons and an advisory marker. | path completion regression |
| Profiles and switching | `config profiles` lists validated named profiles with provider/model/streaming and key presence only; interactive `/model list|show|select` records profile switches. | profile machine-contract regression |
| Session tree | `session tree`, `session clone`, and controlled `session import` expose parent/child metadata and explicitly never replay effects. Interactive `/tree` uses the same service. | session tree/clone/import regression |

## Methodological mapping

Self Forcing is a video-diffusion paper, not something ForgeCode reproduces.
The coding-agent adaptation is methodological: real multi-turn rollouts use
their own prior tool/test outputs; trajectory quality is evaluated as a whole;
old context is replaced by a bounded, source-fingerprinted summary plus a
recent window; and repair feedback has hard step/call/time limits. Long tasks
can still degrade when evidence exceeds configured budgets, so the evaluator
reports failures, latency and unresolved state rather than claiming unlimited
extrapolation.

## Explicit boundaries

F23-F27 remain deferred: no cloud execution, OS sandbox claim, browser/IDE
control, remote MCP marketplace, worktrees, parallel subagents, background
scheduling or automatic commit/push. The command risk classifier and approval
policy are defense in depth, not an operating-system sandbox. The configured
GitHub remote is currently private; changing visibility is an owner decision
required by the assessment's public-repository submission rule.
