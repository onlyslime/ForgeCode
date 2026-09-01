# ForgeCode

> A local coding agent that can inspect, modify, verify, and explain.

[中文文档 / Chinese](README.zh.md) · [Changelog](docs/CHANGELOG.md) · [Examples](docs/demo-script.md)

ForgeCode is a self-built, auditable coding agent for real software work. It
turns a natural-language task into a transparent loop of model decisions,
local tools, file changes, and verification. The important parts—protocol,
AgentLoop, tools, workspace boundary, approvals, and session evidence—live in
this repository instead of behind an agent SDK.

`Local-first` · `Tool calling` · `Streaming` · `Workspace safety` · `Session audit`

<p align="center">
  <img src="show/introduce.gif" alt="ForgeCode interactive terminal demo" width="900">
</p>

## Start in one minute

Choose either installation method on Windows PowerShell.

### Method 1: Install from GitHub

```powershell
irm https://astral.sh/uv/install.ps1 | iex
uv tool install "git+https://github.com/onlyslime/ForgeCode.git"
uv tool update-shell
fcc
```

### Method 2: Install from a GitHub ZIP

Download **Code → Download ZIP** from GitHub, extract it, and open PowerShell
in the extracted `ForgeCode` directory:

```powershell
irm https://astral.sh/uv/install.ps1 | iex
uv sync
uv run forgecode doctor
uv run forgecode
```

To make `fcc` available from any directory, run `uv tool install --editable .`,
then `uv tool update-shell` and reopen PowerShell.

Inside the chat, use `/login` to enter your provider URL, model ID, and API
key. Credentials are stored locally and are never part of the repository.

## A real run

Give ForgeCode an ordinary engineering task, for example:

```text
Read this Python project, find the failing edge case, fix the implementation,
add a regression test, and run the test suite.
```

The interaction is explicit rather than a black box:

```text
◆ assistant
I’ll inspect the project structure and existing tests.

▸ Read src/calculator.py
✓ Read · 42 lines
▸ search "divide"
✓ search · 4 matches

◆ assistant
I found the boundary case and will add a regression test.

▸ Apply patch
  - old behavior
  + corrected behavior
✓ Apply patch
▸ Run tests
✓ Run · exit 0

Completed · Verification passed · Worked for 18.4s · 4 tool steps
```

## What it can do

| Area | Capabilities |
| --- | --- |
| Understand | list and read files, search text/regex, repository map, symbols, definitions, references, metadata |
| Modify | create files, atomic writes, unified patches, red/green previews, transaction records |
| Verify | tests, diagnostics, bounded shell commands, stdout/stderr, exit codes, repair attempts |
| Control | Plan, Act, Bypass, pause, resume, cancel/Esc, safe-boundary steering, follow-up queue, live lifecycle/timing status |
| Context | `AGENTS.md` rules, explicit references, bounded user memory, incremental index, context search, compaction, health diagnostics |
| Git | status, diff, log, worktrees, review, undo and recovery inspection |
| Processes | background commands, status polling, output limits, safe termination |
| Automation | JSON, JSONL, RPC, Python embed API, Node JSONL client |

## How the boundary works

```text
User prompt
    ↓
AgentLoop + provider adapter
    ↓
Validated provider-neutral tool call
    ↓
ToolRegistry
    ↓
WorkspaceGuard + mode + risk + approval
    ↓
Local files, commands, tests
    ↓
Session events, audit, verification
```

The model proposes actions; local code executes them. Every path is validated
against the workspace. Writes and commands are bounded, approved according to
policy, cancellable, and recorded with results. Plan is read-only, Act permits
approved side effects, and Bypass is an explicit trusted-workspace choice.
WorkspaceGuard is an application boundary, not an operating-system sandbox.

Before using Act or Bypass for a real workspace, inspect and explicitly grant
trust from the shell (not from an interactive `/trust` command):

```powershell
fcc trust status
fcc trust grant       # persist trust for the current workspace
fcc trust revoke      # remove that trust later
```

Untrusted workspaces remain available for inspection and Plan mode, while
side-effecting tools are refused until trust is granted.

## Commands worth knowing

Inside `fcc`, start with `/help`, `/tools`, `/status`, `/files`, `/rules`,
`/tree`, `/review`, `/context`, `/compact`, `/events`, `/steer`, `/memory`, `/cancel`, and
`/exit`. `/steer <message>` guides an active run before its next model request;
it never interrupts a tool side effect.
Workspace memory is explicitly managed with `forgecode memory add/show/remove/clear`
or `/memory add <text>` inside `fcc`, and is treated as untrusted context.
Use `!command` to send a bounded command result to the model, or `!!command` to
keep it local. For scripts and CI:

```powershell
fcc --print "review this project" --jsonl
fcc --jsonl
```

## Documentation

- [Chinese documentation](README.zh.md)
- [Demo script](docs/demo-script.md)
- [Changelog](docs/CHANGELOG.md)
- [Documentation guide](docs/README.md)
- [Architecture](docs/architecture.md)
- [Implemented capabilities](docs/implemented-features.md)
- [CLI reference](docs/cli-reference.md) · [Configuration](docs/configuration.md)
- [Security model](docs/security-model.md) · [Session lifecycle](docs/session-lifecycle.md)
- [RPC schema](docs/rpc-schema.md) · [Troubleshooting](docs/troubleshooting.md)
- [Extension guide](docs/extension-guide.md) · [Contributing](docs/contributing.md)

## Repository layout

```text
src/forgecode/   protocol, AgentLoop, providers, tools, security, storage, CLI
tests/            deterministic regression suite
docs/             architecture notes, examples, research, and history
sdk/node/         small JSONL client
```

MIT licensed. Current release: `v1.0.0`.
