# ForgeCode Architecture

ForgeCode is a small provider-neutral local coding agent. It owns the protocol conversion, conversation history, tool execution, loop termination, error propagation, and safety boundary instead of importing an agent framework.

```text
CLI (doctor/tools/run)
  -> AgentLoop
       -> ContextBuilder (system instructions + bounded history)
       -> ModelProvider
            -> OpenAICompatibleProvider (urllib, Chat Completions)
            -> DemoProvider (deterministic offline provider)
       -> ToolRegistry (JSON schemas + dispatch)
            -> WorkspaceGuard (resolved workspace paths, symlink boundary)
            -> list_files / read_file / search / write_file
            -> run_command (approval + timeout + stdout/stderr/exit code)
       -> SessionStore (redacted append-only JSONL events)
```

## Request/response flow

1. `forgecode run` resolves a workspace and creates an approval policy, registry, session store, provider, and `AgentLoop`.
2. The loop sends a system message describing the workspace root, available tools, approval behavior, and verification requirement, followed by the user prompt.
3. The provider converts provider-neutral `Message` values and tool definitions to the OpenAI-compatible Chat Completions JSON shape. It converts assistant text, `finish_reason`, and every tool call (`id`, function name, JSON object arguments) back to `ModelResponse`.
4. The loop appends the assistant message unchanged, executes each tool call through the registry, and appends one tool result per call with the matching `tool_call_id`. Unknown tools, invalid arguments, denied approvals, exceptions, non-zero exits, and timeouts are ordinary structured tool results for the next model turn.
5. The loop stops on a final assistant message, provider/protocol error, empty or invalid response, user interruption, repeated identical calls, verification failure, or a configurable maximum step count. A final assistant message can trigger a bounded verification command; failed verification is returned as context for a limited repair attempt.

## Safety boundary

`WorkspaceGuard` resolves relative and absolute paths and rejects paths outside the root, `..` escapes, and symlink escapes. Read/search operations skip common generated directories and enforce file, line, match, and output limits. `write_file` validates the destination before an approval request and uses a temporary file plus replace for failure-safe writes. `run_command` executes with the workspace as cwd, requires approval, accepts only a 1--120 second timeout, and preserves bounded stdout, stderr, exit code, and timeout metadata.

The default CLI approval policy is interactive. `--auto-approve`/`--yes` is explicit and intended for demos or CI. Approval decisions are emitted as events. The registry catches tool exceptions and turns them into model-visible errors rather than crashing the process.

## Context and auditability

`ContextBuilder` keeps the system and user intent, recent complete messages, and bounded tool arguments/results under a character budget. Older messages are represented by an omission marker. `SessionStore` appends timestamped JSONL events for user messages, model messages, tool calls, approvals, tool results, verification, errors, and final stop reasons. Sensitive key names and configured secret values are replaced with `[REDACTED]`.

## CLI and current limitations

`doctor` reports configuration and tools; `tools` prints schemas; `run` supports a prompt, `--workspace`, `--max-steps`, `--session`, `--verify`, `--auto-approve`, and `--demo`. A run prints bounded progress, verification, status, and Git diff without creating commits. The demo deliberately performs inspect -> write -> failed command -> repair -> verification without network access.

This MVP intentionally does not implement IDE UI, autocomplete, browser/computer control, voice, MCP marketplace, cloud execution, worktrees, parallel subagents, or enterprise governance. The provider and tool contracts leave room for those later extensions without changing the local safety boundary.
