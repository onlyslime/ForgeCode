# Privacy and audit boundary

ForgeCode keeps credentials in environment variables or ignored local files.
Credential values are never serialised into configuration, RPC envelopes,
sessions, checkpoints, telemetry, review artifacts, or provider diagnostics.

## Telemetry

Telemetry defaults to `off`. `offline=true` forces it off and prevents creation
of the local audit file. `local` writes only schema-versioned, bounded scalar
metadata to `.forgecode/telemetry.jsonl`; `on` is an external-capability flag
but ForgeCode currently has no transmitter. Prompt/content, credentials,
commands/arguments, stdout/stderr, workspace paths, environment fields, nested
objects, and strings over 256 characters are dropped and counted. Event labels
are reduced to `[A-Za-z0-9_.-]`. Retention is capped at 5,000 records and export
returns at most 1,000 records with a SHA-256 integrity digest.

Provider failures expose a bounded `ProviderError.to_dict()` diagnostic with
category, retryability, HTTP status, attempt, request id, unresolved-worker
flag, and a 500-character message cap; raw response bodies are not included.

## Durable execution evidence

Telemetry records carry an `event_family` classification (`provider`, `tool`,
`approval`, `transaction`, `recovery`, or `session`). Unknown event names are
retained only as bounded metadata and include an `audit_warning` marker so
privacy reviews can identify producers that have not been classified.
Writes and retention trimming are serialized within the process so concurrent
workers cannot interleave JSONL records or race an atomic trim.

Session and checkpoint records are local execution evidence, not telemetry.
They may contain bounded tool metadata needed for recovery, but use the shared
redaction helpers and never grant permissions. Transaction manifests contain
bounded diffs/hashes required for undo. Trust records contain only canonical
workspace identity, version and grant time. RPC handles/replay caches are
in-memory, bounded, and expire; they do not persist prompts or credential
values.
Act-mode side-effect boundaries revalidate workspace trust before tool
execution; revoking trust during a run therefore fails closed at the next
boundary rather than allowing a queued write or command to proceed.

## Threat model

The framework protects against accidental logging, malformed provider output,
unbounded records, path aliases, stale approvals and cross-workspace access.
It is not an operating-system sandbox: an approved shell command runs with the
user's account privileges. Users should review approval previews, keep secret
files outside the workspace or excluded, and revoke workspace trust when a
project is no longer trusted.
