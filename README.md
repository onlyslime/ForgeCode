# ForgeCode

> A small, auditable coding agent that stays close to the terminal.

ForgeCode is built from the protocol up: model messages, tool schemas, the
agent loop, workspace boundary, approvals, persistence, and verification are
all in this repository. It does not wrap LangChain, an Agents SDK, or another
coding product.

## 目录 · Contents

**中文** · [简介](#中文) · [开始](#开始) · [能力](#能力) · [安全](#安全) · [文档](#文档)
**English** · [Overview](#english) · [Quick start](#quick-start) · [Design](#design) · [Limits](#limits)

## 中文

ForgeCode 是一个本地 coding agent。你给它一句话，它会在指定工作区中理解任务，读取相关文件，调用受控工具，修改代码，运行真实测试，并把每一步写入可审计的 session 记录。它可以接 OpenAI-compatible 服务，也可以用离线 `DemoProvider` 完整演示一次失败—修复—验证闭环。

### 开始

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync
uv run forgecode doctor
```

离线演示（不需要网络或 API key）：

```powershell
$demo = Join-Path $env:TEMP ('forgecode-demo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $demo | Out-Null
uv run forgecode --workspace $demo run --demo --auto-approve
```

在线运行只需提供三个环境变量：

```powershell
$env:FORGECODE_API_KEY = "your-key"
$env:FORGECODE_BASE_URL = "https://api.openai.com/v1"
$env:FORGECODE_MODEL = "your-tool-calling-model"
uv run forgecode chat
```

交互入口也可使用 `fcc`；`fcc --plan`、`fcc --act` 和 `fcc --bypass` 选择运行模式。`/help` 查看命令，`/mode` 切换模式，`/tools`、`/files`、`/skills`、`/tree`、`/review` 查看运行能力和证据，`/quit` 或 `/exit` 退出。

### 能力

- Plan / Act / Bypass：计划模式只读；Act 的写入、命令和测试经过审批；Bypass 仅关闭交互审批，不关闭安全检查。
- 文件工具：列出、读取、搜索、写入、`apply_patch`；显示 bounded diff，并验证路径、编码、大小和符号链接。
- 命令与测试：风险分类、超时、输出上限、取消、子进程清理和环境脱敏；失败结果可反馈给模型进行有限修复。
- 上下文与规则：`AGENTS.md` 规则、路径引用、仓库摘要、增量索引和有界压缩。
- 证据链：session JSONL、checkpoint、事务备份/撤销、review、eval、session tree，以及 Node/Python SDK 和 JSONL RPC。
- 交互体验：固定底部输入栏、多行粘贴、实时工具/模型进度、Worked for 用时、Esc 取消当前任务。

### 安全

所有文件操作都经过 `WorkspaceGuard`；命令有显式 approval、风险策略和有限资源。API key 只从环境变量或被忽略的本地配置读取，日志和模型上下文会脱敏。审批与风险分类是边界，不等同于操作系统 sandbox。

## 文档

- [两分钟演示](docs/demo-script.md)
- [架构与安全](docs/architecture.md)
- [能力审计](docs/implemented-features.md)
- [RPC 与 SDK](docs/rpc-sdk.md)
- [隐私设计](docs/privacy.md)
- [完整更新日志](docs/CHANGELOG.md)
- [评估材料](docs/assignment/)

当前版本：`v0.6.2`。

## English

ForgeCode is a local coding agent built from first principles. It owns the
provider-neutral message protocol, tool registry, AgentLoop, workspace guard,
approval boundary, durable session evidence, and verification path. A request
can become a real inspect → edit → test → repair → verify run, or an entirely
offline deterministic demo. No ready-made agent framework is wrapped here.

### Quick start

```powershell
uv sync
uv run forgecode doctor
uv run forgecode --workspace <dir> run --demo --auto-approve
```

For a live OpenAI-compatible provider, set `FORGECODE_API_KEY`,
`FORGECODE_BASE_URL`, and `FORGECODE_MODEL`, then run `uv run forgecode chat`.
The `fcc` alias supports `--plan`, `--act`, and `--bypass`.

### Design

```text
prompt → bounded context → model/tool calls → guarded local tools
       → structured errors → repair → verification → durable evidence
```

Plan mode is read-only. Act mode asks before side effects. Bypass skips the
interactive approval prompt but keeps workspace validation, hard risk blocks,
timeouts, output limits, cancellation, and audit recording. The same loop is
used by the CLI, offline demo, JSONL RPC, and SDKs.

### Limits

ForgeCode is intentionally local. It is not an IDE, browser controller, cloud
runner, MCP marketplace, parallel-subagent scheduler, or OS-level sandbox.
Those boundaries are explicit so a run remains understandable and reviewable.

See [docs/README.md](docs/README.md) for the documentation map and
[docs/CHANGELOG.md](docs/CHANGELOG.md) for version history.
