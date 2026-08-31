# Security model and threat boundaries

ForgeCode is a local-first policy boundary, not an operating-system sandbox.
The provider, model response, project rules, references, and session text are
all untrusted input.

## Decision order

Workspace canonicalisation and `WorkspaceGuard` run before every filesystem
operation. Tool schemas and argument limits run before dispatch. Mode and tool
policy narrow capabilities, then approval and trust gates protect side effects.
Hash-checked transactions and explicit recovery handle concurrent edits.

Plan is read-only. Act permits approved mutations. Bypass is an explicit trust
escape for a trusted workspace and should not be used with sensitive data.

## Threats addressed

Traversal, forbidden paths, symlink/junction aliases, oversized reads, unsafe
shell forms, control-character injection, secret leakage, stale writes,
TOCTOU replacement, unbounded output, process-tree leaks, and replay of side
effects are rejected or bounded. Cancellation and deadlines are checked at
side-effect boundaries; failures retain exit codes and bounded diagnostics.

## What this does not claim

The framework cannot protect against a malicious operating system, a
compromised interpreter, or a user who grants Bypass/approval. Network
providers may observe prompts according to their own policies; use offline
mode for sensitive demonstrations. Telemetry is local by default and filtered
by the privacy boundary.

