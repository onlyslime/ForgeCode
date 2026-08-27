# Framework Architecture

ForgeCode keeps the coding-agent core independent from any model vendor or agent SDK.

```text
CLI
  -> AgentLoop
       -> ModelProvider (provider-neutral contract)
       -> ToolRegistry (JSON schemas and dispatch)
            -> WorkspaceGuard (path boundary)
            -> Filesystem tools (list/read/search/write)
            -> Shell tool (approval, timeout, exit code)
       -> SessionStore (append-only JSONL)
```

The current `v0.0.1` implementation is a framework skeleton. It already exercises tool dispatch, workspace protection, approval decisions, JSONL events, and loop termination with a fake provider in tests. The next increment should add a real provider adapter, structured response parsing, context budgeting, and an interactive task command. Browser tools, MCP, parallel agents, and cloud execution remain outside the initial local MVP.

## Safety boundary

Every filesystem operation resolves through `WorkspaceGuard`. Side-effecting file writes and shell commands require an `ApprovalPolicy`; the default shell policy denies execution. Shell results preserve output and exit codes, and tool failures are returned as structured results so an agent can decide whether to retry.
