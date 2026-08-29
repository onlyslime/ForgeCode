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

当前版本：`v0.0.44`（版本号与 `VERSION`、`pyproject.toml` 和
`src/forgecode/__init__.py` 保持同步）。

### v0.0.13 CLI harness

- `trust status|grant|revoke` 建立可撤销的工作区信任记录；`login` 仅引用环境变量凭据。
- `rpc` 接受 JSONL 请求并复用 CLI envelope；Node 客户端位于 `sdk/node/index.mjs`。
- RPC/SDK 生命周期与隐私边界见 [`docs/rpc-sdk.md`](docs/rpc-sdk.md) 和 [`docs/privacy.md`](docs/privacy.md)。
- 配置支持 `offline` 与 `telemetry = off|local|on`；offline 强制禁止外发，local 仅写入本地审计 JSONL。
- 交互输入中的 Escape 控制字节会请求取消，并复用既有 cancel/pause 边界；
  对正在运行的在线 provider，是否能由真实 PTY 传递到 worker 仍取决于终端
  与 provider 的取消支持。
- `sessions --state <state>`、RPC `session.list` 以及 Node/Python
  `sessionList`/`session_list` 提供有界、可脚本化的后台会话发现；workspace
  会先规范化并校验，避免跨工作区读取元数据。
- `config policy`/`config.policy` 提供只读的逐工具权限解释，展示配置与运行时
  收窄、Plan/Act、approval、trust 的生效原因。

### v0.0.9 扩展能力

- **滚动有界上下文**：AgentLoop 按序列化消息/工具参数大小自动触发有界压缩；摘要保留目标、安全规则、计划、验证和最近完整 tool pairing，并追加带 source sequence/fingerprint 的 `context_compacted` 证据。达到硬上限时明确报告退化，不假设无限上下文。
- **整条轨迹评估**：`forgecode eval`（别名 `benchmark`）只读取持久化事件，计算完成、真实验证、失败/修复、审批拒绝、重复调用、压缩、冲突、取消、未决和审计指标；模型自评不能制造成功。
- **会话树与路径建议**：`session tree|clone|import` 提供不重放副作用的父子证据；`context complete` 和交互 `/files <prefix>` 返回稳定相对路径及排除原因，结果仅供建议。
- **模型 profile**：`config profiles` 和交互 `/model list|show|select` 展示/切换经过严格校验的 provider 配置，只显示 API key 环境变量名和是否配置，并记录切换事件。

### v0.0.10 交互运行时控制

- **单一可控 worker**：`chat`/`start` 在同一个 AgentLoop 上支持有界 FIFO follow-up；`/pause`、`/resume`、`/cancel` 在 provider、工具、审批和验证安全边界生效。恢复会校验 session/checkpoint 与规则、计划、配置指纹；无法停止的 worker 进入 recovery-required。
- **机器契约与模型竞态**：`chat --jsonl` 保证 stdout 每行是可解析 envelope，进度/审批只写 stderr；运行期间 `/model select` 安全拒绝且不改变 provider。旧 `InteractiveSession` 和 `--json` 入口保持兼容。

### v0.0.11 Pi-inspired 终端快捷方式

- **`!<command>` / `!!<command>`**：两者都复用受控 `run_command`、审批、风险分类、超时、取消和脱敏；单 `!` 将有界结果交给下一轮模型，双 `!!` 只返回用户和审计，不触发 provider。快捷方式只接受行首前缀，Plan 模式和危险命令仍会拒绝。

### v0.0.12 Pi-inspired 工具策略

- **运行时工具收窄**：`chat`/`start`/`run` 支持 `--tools read_file,search`、`--exclude-tools run_command` 和 `--no-tools`。它们只会进一步收窄配置中的 `tool_policy`，并一致作用于 provider schema、verification、快捷方式和 AgentLoop；禁用工具 fail-closed，不执行命令。

### v0.0.8 扩展能力

- **命名测试配置**：`.forgecode/tests.toml` 使用严格 TOML schema；每个 profile
  是不可注入的 argv 数组，可声明工作目录、setup/teardown、非敏感环境变量白名单、
  stdout/stderr/总量额度、超时和允许的退出码。`test` CLI 的 list/show/run
  使用该执行器；交互式 `/test` 保留兼容的验证命令入口并共享审批、
  超时、脱敏和 session 审计边界。两种入口都记录有限输出与结果状态。
- **证据驱动 review**：`review` 聚合 session、plan、context、transaction、
  test、hook 和 diff hunk 证据，运行 secrets、forbidden-path、suspicious-command
  与 Python AST syntax 检查；报告只引用相对路径、序列号和 SHA-256，不接受模型文字
  直接宣称通过。`review --export` 生成绑定工作区的摘要，`--verify` 检查篡改、过期
  文件和 artifact digest。
- **取消与未决恢复**：`CancellationToken` 和 deadline 从 AgentLoop 传到 provider、
  测试进程及 SSE 解析器；无法确认终止的 worker 会被标记 `unresolved` 并进入恢复证据，
  不会把晚到响应当成成功，也不会在副作用后自动重放工具调用。provider retry/attempt
  事件带 request id、attempt id 和结果分类。
- **严格机器接口**：`--jsonl` 输出单行 envelope（`schema_version/kind/ok/command`
  加互斥的 `data` 或 `error`），进度和审批提示不污染 stdout；旧 `--json` 的必要兼容
  形状仍保留。退出码区分成功、执行失败、输入错误、恢复冲突和取消。

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

The v0.0.9 release adds automatic rolling compaction, durable trajectory
evaluation, safe path completion, profile inspection/switch auditing and a
non-replaying session tree on top of the v0.0.8 strict named test profiles,
evidence-driven review and provider cancellation/deadline propagation.
`test list|show|run` accepts only bounded argv profiles; `review` joins the
session/plan/context/transaction/test/hook ledger and can export or verify a
workspace-bound digest artifact. `--jsonl` keeps one parseable envelope per
line, while progress and approval prompts stay on stderr. The v0.0.7 Markdown
skill manifest, incremental context index, provider health diagnostics and
lifecycle hooks remain available. None of these extensions grants implicit
shell, write, network or secret access.

The v0.0.10 release adds a single-worker interactive control surface: bounded
FIFO follow-ups, safe `/pause`, `/resume`, `/cancel`, checkpoint/fingerprint
resume validation, active model-switch rejection and bounded shutdown. It
does not claim Escape-specific terminal support or an operating-system sandbox.

The v0.0.11 release adds Pi-inspired `!<command>` and `!!<command>` shortcuts;
the single prefix sends a bounded redacted result to one provider turn, while
the double prefix remains local and never calls a provider. Both reuse the
existing approval, risk, timeout, cancellation, workspace and session bounds.

The v0.0.12 release adds runtime tool narrowing. `chat`/`start`/`run` accept
`--tools`, `--exclude-tools`, and `--no-tools`; configuration and CLI policies
compose monotonically, and the effective registry is shared by provider
schemas, verification, AgentLoop, and shortcuts. Disabled tools fail closed
without spawning a command.

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
uv run forgecode config profiles
uv run forgecode context complete src
uv run forgecode session tree --jsonl
uv run forgecode eval latest --jsonl
uv run forgecode context index
uv run forgecode context search "AgentLoop"
uv run forgecode test list
uv run forgecode test show default --jsonl
uv run forgecode review --jsonl
uv run pytest
```

`review` deliberately reports non-zero when the inspected tree contains
credential-shaped test fixtures or other findings; that is a finding report,
not a crash. Use the fresh offline demo above to see a clean pass, or inspect
the bounded `findings`/`checks` fields in JSONL output.

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
`context explain|diagnostics`, `test list|show`, and `review`/`transaction`.
`test run` is side-effecting because it starts a project process; it still uses
the profile approval policy and records bounded evidence. Machine-readable
commands use `--json` or `--jsonl`; the latter emits one strict envelope per
line. Exit codes are 0 success, 1 execution or audit failure, 2 invalid input
or unavailable resource, 3 recovery/hash/config conflict, and 130
cancellation.

## Repository layout

```text
src/forgecode/       provider, protocol, loop, lifecycle, context, rules, references, plan, application services, tools, security, storage, CLI
tests/               deterministic offline regression tests
docs/assignment/     assessment PDF
docs/research/       research plan and feature report
docs/demo-script.md  reproducible two-minute demo script
docs/implemented-features.md  claimed capabilities and manual audit status
docs/goals/          ignored timestamped goal prompts (private)
docs/strategy/       ignored local planning/status notes
docs/releases/       ignored historical roadmaps and acceptance reports
```

See [docs/README.md](docs/README.md) for the documentation map, and
[docs/architecture.md](docs/architecture.md) for data flow and safety
semantics, [docs/demo-script.md](docs/demo-script.md) for the assessment
walk-through, [AGENTS.md](AGENTS.md) for repository rules, and
[docs/VERSIONING.md](docs/VERSIONING.md) for version policy, and
[docs/implemented-features.md](docs/implemented-features.md) for the maintained
implementation audit. Historical release evidence remains locally under
`docs/releases/` and is intentionally not committed.

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
