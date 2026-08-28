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

当前版本：`v0.0.7`。

### v0.0.7 扩展能力

- **Skills manifest**：`skills list|check|show|run` 发现显式 Markdown skill，严格校验 id、版本、输入 schema、权限、审批和配额；Markdown skill 默认只提供提示内容，不能自行获得写入、Shell、网络或密钥权限。
- **增量上下文索引**：`context index|search|show|clear` 在 ignored 的 `.forgecode` 中维护本地 JSON 索引，按 digest 增量更新，搜索支持关键词、正则、符号、glob 和路径过滤；返回行号、bounded snippet、digest 和检索原因，文件变化或敏感内容会被排除。
- **Provider diagnostics**：`provider health` 只读展示模型能力、streaming 和配置状态，不发起网络请求；运行时继续使用统一的 retry、SSE 校验、取消/超时和 session 审计路径。
- **Lifecycle hooks**：工具和模型前后置事件可被受控 observer 记录；声明 `fail_closed` 的 hook 出错会阻止操作，默认 observer 不改变审批决定，递归调用会被拒绝。

### v0.0.6 基础工作流

- **作用域规则**：`rules show/check` 从根目录及目标目录链加载
  `AGENTS.md`，展示 source、scope、priority、digest、截断和冲突；规则只是
  不可信上下文，不能绕过安全策略。
- **精确上下文**：任务文本支持 `@relative/file.py`、带引号路径、bounded
  目录以及 `@git:status`、`@git:diff`、`@git:log`；敏感、二进制、ignored
  和越界路径不会进入模型。
- **结构化计划与交互**：`plan` 生成有 schema/revision/DAG/status/evidence
  的计划；`chat` 提供 `/help`、`/status`、`/plan`、`/mode`、`/rules`、
  `/files`、`/review`、`/test`、`/compact`、`/undo`、`/quit`。
- **恢复与撤销**：completed session 默认 inspect-only；`--fork` 或
  `session fork` 生成带 parent evidence 的新 run。压缩只追加 factual
  summary，不重写 JSONL。写入/patch 保存 ignored content-addressed backup，
  `transaction --execute` 在 after hash 一致时跨进程撤销。
- **配置与流式响应**：`.forgecode/config.toml`（ignored）提供 typed profile
  和 tool narrowing policy，优先级为 CLI > TOML > environment > defaults；
  API key 仍只能来自环境。可选 SSE 会先完整组装并校验 tool call，断流执行
  零工具。

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

The v0.0.7 release adds a strict Markdown skill manifest and a local
incremental context index. `skills list|check|show|run` and
`context index|search|show|clear` are read-only, bounded, JSON-capable
interfaces; index snippets are digest-checked before use. `provider health`
reports capabilities without a network request, while lifecycle hooks provide
auditable before/after tool and model observations with optional fail-closed
behavior. These additions do not grant extensions implicit shell, write,
network or secret access.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run forgecode doctor
uv run forgecode tools
uv run forgecode rules show
uv run forgecode config validate
uv run forgecode provider health
uv run forgecode skills list
uv run forgecode context index
uv run forgecode context search "AgentLoop"
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
uv run forgecode --workspace $demo transaction
uv run forgecode --workspace $demo transaction latest --execute --auto-approve
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
redacted JSONL and checkpoint metadata under `.forgecode/` and print bounded
status/diff; they never create commits automatically. Use `inspect`/`map` for a
deterministic repository snapshot, `sessions`/`session show|export` for audit,
`status`/`diff` for transaction evidence, and `run --resume ID --dry-run` to
preview safe recovery. Recovery conflicts return exit code 3 and never replay
side effects automatically.

Interactive offline demo (input is pipeable and therefore testable):

```powershell
$lines = @('/help', 'inspect calculator', '/mode act', 'fix calculator', '/review', '/compact', '/quit')
$lines | uv run forgecode --workspace $demo chat --demo --auto-approve
```

Useful read-only commands include `plan`, `rules show|check`, `config
show|validate`, `session show|export|inspect|compact|fork`, `status`, `diff`,
and `review`/`transaction`. `review` joins the latest ledger with session
plan, references, verification checks and audit metrics. Machine-readable
commands use `--json` or `--jsonl`; interactive JSON mode emits one JSON object
per line. Exit codes are 0 success, 1 execution or
audit failure, 2 invalid input/unavailable resource, 3 recovery/hash/config
conflict, and 130 cancellation.

## Repository layout

```text
src/forgecode/       provider, protocol, loop, lifecycle, context, rules, references, plan, application services, tools, security, storage, CLI
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
