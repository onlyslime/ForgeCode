# ForgeCode

ForgeCode is a self-built, auditable coding-agent framework. It is designed for the graduate recommendation assessment and keeps the agent core under this repository's control instead of wrapping an existing agent product.

## 中文简介

ForgeCode 是一个自行实现的 coding agent 框架。它负责模型协议、会话状态、工具注册、本地文件与命令执行、AgentLoop、错误处理和安全边界。项目不依赖 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI 等现成 agent 框架。

当前版本：`v0.0.2`。仓库已包含可运行 CLI、工作区路径保护、文件/搜索/写入/命令工具、JSONL 会话存储、模型适配器接口、AgentLoop 骨架和测试。

## English overview

The framework separates `ModelProvider`, `AgentLoop`, `ToolRegistry`, `WorkspaceGuard`, and `SessionStore`. Built-in tools expose file listing, UTF-8 reading, regex search, approved writes, and approved shell execution. Tool results preserve errors and exit codes so a future provider can perform a verifiable repair loop.

## Quick start

```powershell
uv sync
uv run forgecode doctor
uv run forgecode tools
uv run pytest
```

The current release is framework-only; configure a real model adapter in a later increment. Put credentials in environment variables or a local ignored `.env` file. Never commit secrets.

## Repository layout

```text
src/forgecode/       framework source
tests/               automated tests
docs/assignment/     assessment PDF
docs/research/       research plan and report
docs/goals/          ignored local goal prompts
```

## Safety and versioning

All paths are checked against the selected workspace. Writes and commands require an approval policy, and commands have bounded timeouts. Every commit/tag uses `vA.B.C`: ordinary work increments C; A or B changes require an explicit owner request and reset the lower components. See [AGENTS.md](AGENTS.md), [architecture](docs/architecture.md), and [versioning](docs/VERSIONING.md).
