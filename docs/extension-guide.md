# Extension guide: rules, skills, and hooks

Extensions add context or bounded lifecycle behavior; they never grant
capabilities. The built-in policy, workspace guard, approval, timeout, and
redaction boundaries always win.

## Rules and memory

`rules show|check|explain` reports scoped sources, precedence, fingerprints,
and diagnostics. Rules and explicit workspace memory are untrusted context;
they may guide a plan but cannot approve a tool. Keep secrets out of both.

## Skills

Place a Markdown skill or a manifest in a discovered skills directory, then run
`skills check` and `skills show NAME`. Markdown skills are instructional.
Executable skills require explicit approval, bounded arguments, a timeout, and
the same workspace policy as any other command. State changes are managed with
the validated enable/disable/remove/restore operations; inspect the resulting
manifest before running it.

## Hooks

Hooks observe lifecycle events such as provider calls, approvals, tools,
transactions, and recovery. They receive bounded, privacy-filtered metadata.
Hook failures, timeouts, and cleanup errors are isolated and recorded; a hook
cannot widen tool access or bypass approval. Keep hooks deterministic and
idempotent so retries do not duplicate external effects.

## Review checklist

Run `skills check`, `rules check`, the relevant named test profile, and
`review --json` before sharing an extension. Do not commit credentials,
runtime state, generated logs, or local `.forgecode/` files.

