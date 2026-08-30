# ForgeCode

> A local coding agent built from the protocol up.

ForgeCode is a compact, auditable terminal harness. It owns the model
protocol, tool registry, AgentLoop, workspace boundary, approvals, session
evidence, and verification path. It is deliberately readable and hackable;
there is no wrapped agent SDK hiding the important parts.

[中文文档](README.zh.md)

## Quick start

The recommended command is `fcc`. The following two paths cover a new machine
and a repository downloaded as a ZIP.

### New Windows machine

Install [uv](https://docs.astral.sh/uv/) in PowerShell:

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

Open a new PowerShell window, then install ForgeCode directly from GitHub:

```powershell
uv tool install "git+https://github.com/onlyslime/ForgeCode.git"
uv tool update-shell
```

Open a new PowerShell window once more. `fcc` is now available from any
directory:

```powershell
fcc --version
fcc
```

### GitHub ZIP download

Extract the ZIP and open PowerShell in the extracted project directory. Install
ForgeCode as a global tool:

```powershell
uv tool install .
uv tool update-shell
```

Open a new PowerShell window and use `fcc` from any directory:

```powershell
fcc --version
fcc
```

For an online provider, run ForgeCode and enter the three values it gives you:

```sh
fcc
/login
# URL, ID, KEY
```

The short command is `fcc`; pass `--plan`, `--act`, or `--bypass` to choose a
mode. Connection values are kept in local ForgeCode state, not the repository.

When an interactive Act or Bypass session opens an untrusted workspace,
ForgeCode asks whether to trust that directory for side effects. Answer `y` to
save the local trust record, or press Enter to continue with side effects
denied. The equivalent explicit command is `forgecode trust grant`.

## What it does

Give ForgeCode a task. It inspects the workspace, calls bounded tools, edits
files, runs real tests, repairs failures within limits, and reports what
happened. The same loop powers the terminal, offline demo, JSONL RPC, and
Node/Python SDKs.

- file listing, reading, search, writing, and unified `apply_patch`
- Plan, Act, and Bypass modes with explicit side-effect boundaries
- command and test execution with timeout, cancellation, output, and process limits
- rules, references, repository context, compaction, context health, event timelines, and model/tool progress
- durable sessions, checkpoints, transaction undo, review, evaluation, and audit JSONL
- skills, hooks, provider diagnostics, path completion, and machine-readable envelopes

## Interactive commands

Type `/help` in `fcc` for the complete list. Useful starting points are
`/mode`, `/tools`, `/files`, `/skills`, `/rules`, `/tree`, `/review`,
`/compact`, `/context`, `/events`, `/cancel`, `/quit`, and `/exit`. `/events` can
filter recent audit entries, for example `/events 20 error`. `!command` sends a bounded command
result to the model; `!!command` keeps it local.

For a deliberately narrow run, use an audited group such as
`fcc --tools read_only` or `fcc --exclude-tools execution`. Groups expand
against tools registered in the current mode; they narrow runtime policy and
are not an OS sandbox.

## Architecture

ForgeCode keeps the complete agent boundary in this repository:

```text
CLI / interactive UI
  -> application services and typed configuration
  -> rules, references, and task plan
  -> AgentLoop
       -> provider adapter (OpenAI-compatible or offline demo)
       -> ContextBuilder and bounded history
       -> ToolRegistry (schemas, validation, mode policy)
            -> WorkspaceGuard -> file tools and structured patches
            -> command/test runners (risk, approval, timeout)
       -> sessions, checkpoints, transactions, review and audit JSONL
```

Each model turn uses provider-neutral messages and validated tool calls. Tool
results, errors, approvals, timeouts, cancellations, and verification evidence
are returned to the model and persisted with matching call IDs. Plan is
read-only; Act requires approval for side effects; Bypass is an explicit
trusted-workspace choice. Paths pass through `WorkspaceGuard`, writes are
atomic, patches are previewed and reversible, and dangerous commands are
hard-blocked. The classifier is a policy boundary, not an OS sandbox.

## Capabilities

- Provider-neutral tool calling with OpenAI-compatible and deterministic offline
  providers, retry/deadline handling, SSE validation, and cancellation.
- Workspace-aware listing, UTF-8 reading, search, summaries, multi-file patches,
  atomic writes, and redacted bounded output.
- Plan, Act, and Bypass modes; risk classification, approval, timeout/output
  limits, process-tree termination, and runtime tool narrowing.
- Scoped `AGENTS.md` rules, explicit references, incremental context indexing,
  skills, and lifecycle hooks with validation and quotas.
- Durable sessions/checkpoints, hash-aware transactions and undo, pause/resume/
  cancel/Escape, compaction, session tree/import, and recovery inspection.
- Named tests, bounded verification/repair, evidence-driven review/export,
  trajectory evaluation, provider diagnostics, telemetry status, and Python/
  Node JSONL SDKs.
- Human REPL and strict JSON/JSONL interfaces share the same safety contracts;
  progress, errors, exit codes, and audit metadata remain visible.

## Repository

```text
src/forgecode/   protocol, loop, providers, tools, security, storage, CLI
tests/            deterministic regression suite
docs/             architecture, design notes, examples, and history
sdk/node/         small JSONL client
```

The project is intentionally local. It is not an IDE, browser controller,
cloud runner, or operating-system sandbox. Read the [examples](docs/demo-script.md)
and [changelog](docs/CHANGELOG.md) for details.

Current release: `v0.7.0`.

## License

MIT
