# ForgeCode

ForgeCode is a self-built, auditable local coding-agent framework for the
graduate recommendation assessment. It owns the provider-neutral protocol,
conversation loop, tool schemas and local execution, context limits, error
handling, approval boundary, and JSONL audit log. It does not wrap LangChain,
LlamaIndex, an Agents SDK, or another ready-made coding-agent product.

## 中文简介

ForgeCode 是一个自行实现的本地 coding agent：模型通过 OpenAI-compatible
Chat Completions 返回结构化 tool calls，ForgeCode 在用户指定工作区中列出
文件、读取和搜索 UTF-8 文本、生成安全 patch、审批写入、运行命令、接收真实
测试失败并进行有限修复和验证。

核心能力包括：

- **Plan/Act 权限边界**：plan 只暴露摘要、列文件、读文件和搜索；工具执行层
  即使收到伪造调用也会返回 `mode_denied`。act 允许副作用，但每次写入、patch
  和命令仍经过审批。
- **结构化 `apply_patch`**：支持 unified diff 和 `*** Begin Patch`，多文件/多
  hunk、行偏移、新建和显式删除；执行前检查路径、UTF-8、大小、上下文和符号
  链接边界，内存预验证后才原子写入，并展示有上限的 diff preview。
- **命令安全**：命令有 normal、filesystem_destructive、privilege_or_system、
  network_or_remote、repository_irreversible 风险分类；危险不可逆模式硬拒绝，
  其余命令需审批，拥有 1--120 秒超时、进程终止、stdout/stderr/退出码上限和
  子进程环境脱敏。
- **轻量上下文与审计**：workspace summary 提供语言、构建文件、测试目录和
  Git 状态；上下文、工具输出和 session JSONL 都有大小限制，并递归脱敏常见
  API key/token/password/cookie 形态。
- **真实离线演示**：DemoProvider 在隔离工作区读取有 bug 的 calculator，先跑
  出失败测试，再走 patch 和审批，最后取得真实通过结果；不需要网络或 API key。

当前版本：`v0.0.4`。

## English overview

The runtime is a provider-neutral loop:

```text
prompt -> bounded context -> model response/tool calls -> local tools
       -> structured errors/results -> repair -> verification -> report
```

`OpenAICompatibleProvider` uses the Python standard library and reads
`FORGECODE_API_KEY`, `FORGECODE_BASE_URL` (default
`https://api.openai.com/v1`), and `FORGECODE_MODEL` from the environment. The
offline `DemoProvider` follows the same `AgentLoop` and `ToolRegistry` path.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run forgecode doctor
uv run forgecode tools
uv run pytest
```

Read-only planning in the current workspace:

```powershell
$workspace = (Get-Location).Path
uv run forgecode --workspace $workspace run --mode plan "inspect the project and propose a fix"
```

Offline assessment demo (use a fresh directory; it refuses fixture conflicts):

```powershell
$demo = Join-Path ([System.IO.Path]::GetTempPath()) ('forgecode-demo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $demo | Out-Null
uv run forgecode --workspace $demo run --demo --auto-approve
```

For a real provider, keep credentials outside Git:

```powershell
$env:FORGECODE_API_KEY = "your-key"
$env:FORGECODE_BASE_URL = "https://api.openai.com/v1"
$env:FORGECODE_MODEL = "your-tool-calling-model"
uv run forgecode --workspace . run --mode act "inspect and implement a small tested fix" --verify "uv run pytest"
```

Act mode asks before side effects by default. `--auto-approve`/`--yes` is an
explicit approval choice for demos or CI; it never bypasses workspace checks,
hard risk blocks, timeouts, output limits, or session recording. Runs store
redacted JSONL under `.forgecode/` and print bounded status/diff; they never
create commits automatically.

## Repository layout

```text
src/forgecode/       provider, protocol, loop, tools, security, storage, CLI
tests/               deterministic offline regression tests
docs/assignment/     assessment PDF
docs/research/       research plan and feature report
docs/demo-script.md  reproducible two-minute demo script
docs/goals/          ignored timestamped goal prompts (private)
```

See [docs/architecture.md](docs/architecture.md) for data flow and safety
semantics, [docs/demo-script.md](docs/demo-script.md) for the assessment
walk-through, [AGENTS.md](AGENTS.md) for repository rules, and
[docs/VERSIONING.md](docs/VERSIONING.md) for version policy.

## Safety and scope

All filesystem paths pass through `WorkspaceGuard`, including symlink checks.
Writes use failure-safe temporary replacement where practical. Commands run in
the workspace with bounded timeout/output and a conservative heuristic risk
policy; this is an approval boundary, not a complete OS sandbox. The project
intentionally excludes IDE UI, autocomplete, browser/computer control, voice,
MCP marketplace, cloud execution, worktrees, parallel subagents, background
scheduling, and enterprise governance.

Versioning uses `vA.B.C`: an ordinary commit increments only C; A/B changes
require an explicit owner request and reset lower components.
