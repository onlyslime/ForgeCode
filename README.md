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

Extract the ZIP, open PowerShell in the extracted project directory, and let
uv create the project environment:

```powershell
uv sync
uv run forgecode doctor
uv run fcc
```

`uv sync` is the project/development path; it does not install a global
command. To make this checkout's `fcc` available from every directory, run
the following once from the project directory:

```powershell
uv tool install --editable .
uv tool update-shell
```

After opening a new PowerShell window, use `fcc` anywhere. The editable
installation follows the extracted directory, so keep it in place.

For an online provider, run ForgeCode and enter the three values it gives you:

```sh
fcc
/login
# URL, ID, KEY
```

The short command is `fcc`; pass `--plan`, `--act`, or `--bypass` to choose a
mode. Connection values are kept in local ForgeCode state, not the repository.

## What it does

Give ForgeCode a task. It inspects the workspace, calls bounded tools, edits
files, runs real tests, repairs failures within limits, and reports what
happened. The same loop powers the terminal, offline demo, JSONL RPC, and
Node/Python SDKs.

- file listing, reading, search, writing, and unified `apply_patch`
- Plan, Act, and Bypass modes with explicit side-effect boundaries
- command and test execution with timeout, cancellation, output, and process limits
- rules, references, repository context, compaction, and model/tool progress
- durable sessions, checkpoints, transaction undo, review, evaluation, and audit JSONL
- skills, hooks, provider diagnostics, path completion, and machine-readable envelopes

## Interactive commands

Type `/help` in `fcc` for the complete list. Useful starting points are
`/mode`, `/tools`, `/files`, `/skills`, `/rules`, `/tree`, `/review`,
`/compact`, `/cancel`, `/quit`, and `/exit`. `!command` sends a bounded command
result to the model; `!!command` keeps it local.

## Repository

```text
src/forgecode/   protocol, loop, providers, tools, security, storage, CLI
tests/            deterministic regression suite
docs/             architecture, design notes, examples, and history
sdk/node/         small JSONL client
```

The project is intentionally local. It is not an IDE, browser controller,
cloud runner, or operating-system sandbox. Read the [architecture](docs/architecture.md),
[examples](docs/demo-script.md), [capability audit](docs/implemented-features.md),
and [changelog](docs/CHANGELOG.md) for details.

Current release: `v0.6.3`.

## License

MIT
