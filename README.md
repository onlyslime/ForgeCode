# ForgeCode

ForgeCode is a self-built, auditable local coding-agent framework for the graduate recommendation assessment. The core protocol, loop, tools, safety boundary, context budget, error handling, and session log are implemented in this repository; no ready-made agent framework or product is wrapped.

## 中文简介

ForgeCode 是自行实现的本地 coding agent。它通过 OpenAI-compatible Chat Completions 接口接收模型的结构化 tool calls，并在用户选定的工作区内完成：列文件、读取 UTF-8 文件、文本/正则搜索、经审批写文件、经审批运行命令、把 stdout/stderr/退出码和错误回传模型、有限修复、验证和最终结果输出。

当前版本：`v0.0.3`。核心模块彼此分离：`ModelProvider` 负责模型协议，`AgentLoop` 负责循环和终止，`ToolRegistry` 负责工具 schema/分发，`WorkspaceGuard` 负责路径边界，`SessionStore` 负责脱敏后的 JSONL 审计事件。

## English overview

The MVP is a provider-neutral loop:

```text
prompt -> model response -> structured tool calls -> local tools
       -> bounded errors/results -> repair attempt -> verification -> diff/report
```

`OpenAICompatibleProvider` uses the Python standard library and reads `FORGECODE_API_KEY`, `FORGECODE_BASE_URL` (default `https://api.openai.com/v1`), and `FORGECODE_MODEL` from the environment. A deterministic `DemoProvider` exercises the complete flow without network access.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run forgecode doctor
uv run forgecode tools
uv run pytest
uv run forgecode --workspace . run --demo --auto-approve
```

For a real provider, set credentials outside Git (for example in an ignored `.env` loaded by your shell):

```powershell
$env:FORGECODE_API_KEY = "your-key"
$env:FORGECODE_BASE_URL = "https://api.openai.com/v1"
$env:FORGECODE_MODEL = "your-tool-calling-model"
uv run forgecode --workspace . run "Inspect the project and add a small tested improvement" --verify "uv run pytest"
```

The default mode asks before writes and commands. `--auto-approve`/`--yes` is an explicit, less-safe option for demonstrations and CI. Runs write redacted JSONL events under `.forgecode/` and print changed-file status plus a bounded `git diff`; runs never create commits automatically.

## Repository layout

```text
src/forgecode/       provider, loop, tools, security, storage, CLI
tests/               deterministic offline tests
docs/assignment/     assessment PDF
docs/research/       research plan and feature report
docs/goals/          ignored timestamped goal prompts (private)
```

## Safety and scope

All filesystem paths resolve through `WorkspaceGuard`, including symlink checks. Writes are atomic where practical, commands run in the workspace with a 1--120 second timeout, and outputs are bounded. Session events redact sensitive keys and configured secret values. The current scope intentionally excludes IDE UI, MCP marketplace, browser/computer control, voice, cloud execution, parallel agents, worktrees, autocomplete, and enterprise governance; these are later extensions, not hidden dependencies.

Versioning follows `vA.B.C`: ordinary work increments only C; A/B changes require an explicit owner request and reset lower components. See [AGENTS.md](AGENTS.md), [architecture](docs/architecture.md), and [versioning](docs/VERSIONING.md).
