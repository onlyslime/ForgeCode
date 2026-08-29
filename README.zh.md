# ForgeCode

> 一个从协议开始自己造的本地 coding agent。

ForgeCode 是一个简洁、可审计的终端工具。它自己处理模型消息、工具、AgentLoop、工作区、审批、会话记录和验证流程；代码保持开放、直接、方便改造。

## 安装与开始

推荐使用 `fcc`。下面分别说明空白电脑和 GitHub ZIP 两种情况。

### 空白 Windows 电脑

在 PowerShell 安装 [uv](https://docs.astral.sh/uv/)：

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

重新打开 PowerShell，直接从 GitHub 安装 ForgeCode：

```powershell
uv tool install "git+https://github.com/onlyslime/ForgeCode.git"
uv tool update-shell
```

再次打开 PowerShell 后，`fcc` 可在任意目录使用：

```powershell
fcc --version
fcc
```

### 已下载 GitHub ZIP

解压 ZIP，在解压后的项目目录打开 PowerShell，直接安装全局命令：

```powershell
uv tool install .
uv tool update-shell
```

重新打开 PowerShell 后，`fcc` 就能在任意目录使用：

```powershell
fcc --version
fcc
```

配置模型时，在 `fcc` 中输入 `/login`，填写服务商提供的 URL、ID 和 KEY：

```powershell
fcc --act
/login
# URL、ID、KEY
```

在 Act 或 Bypass 模式打开未信任的工作区时，ForgeCode 会询问是否信任该目录
以执行副作用。输入 `y` 会保存本地信任记录，直接回车则继续但拒绝副作用。
也可以使用显式命令：`forgecode trust grant`。

## 它能做什么

你提出任务，ForgeCode 会查看相关文件，调用工具，修改代码，运行测试，处理失败，并给出有证据的结果。

- 列文件、读取、搜索、写入和 unified `apply_patch`
- Plan、Act、Bypass 三种工作模式
- 带超时、取消和输出限制的命令与测试执行
- 规则、引用、仓库上下文、上下文健康度、事件时间线和上下文压缩
- session、checkpoint、事务撤销、review、eval 和 JSONL 审计
- skills、hooks、Node/Python SDK 和 JSONL RPC
- 固定输入栏、多行粘贴、实时进度、事件筛选和 Esc 取消

## 交互命令

在 `fcc` 中输入 `/help` 查看全部命令。常用命令包括 `/mode`、`/tools`、`/files`、`/skills`、`/rules`、`/tree`、`/diff`、`/context`、`/events`、`/review`、`/compact`、`/cancel`、`/quit` 和 `/exit`。`/events 20 error` 可筛选最近的错误事件。

## 架构

ForgeCode 将 agent 的完整边界保留在本仓库中：

```text
CLI / 交互界面
  -> 应用服务与类型化配置
  -> rules、references、任务计划
  -> AgentLoop
       -> 模型适配器（OpenAI 兼容或离线 demo）
       -> ContextBuilder 与有界历史
       -> ToolRegistry（schema、校验、模式策略）
            -> WorkspaceGuard -> 文件工具与结构化 patch
            -> 命令/测试执行器（风险、审批、超时）
       -> session、checkpoint、transaction、review、JSONL 审计
```

每轮模型响应都会转换为统一消息并校验 tool call。工具结果、错误、审批、
超时、取消和验证证据都会回传模型，并以匹配的调用 ID 持久化。Plan 只读；
Act 的副作用需要审批；Bypass 是明确的可信工作区选择。所有路径经过
`WorkspaceGuard`，写入采用原子替换，patch 会预览且可撤销，危险命令会硬拦截。
命令分类器是策略边界，不是操作系统沙箱。

## 能力清单

- 与供应商无关的 tool calling，支持 OpenAI 兼容和确定性的离线 provider，
  以及重试、截止时间、SSE 校验和取消。
- 工作区文件列表、UTF-8 读取、搜索、仓库摘要、多文件 patch、原子写入和
  脱敏的有界输出。
- Plan、Act、Bypass 三种模式；风险分类、审批、超时/输出限制、进程树终止
  和运行时工具收窄。
- scoped `AGENTS.md` 规则、显式上下文引用、增量索引、skills，以及带校验
  和配额的生命周期 hooks。
- 持久化 session/checkpoint、哈希冲突事务与 undo、暂停/恢复/取消/Escape、
  上下文压缩、session tree/import 和恢复检查。
- 命名测试、有界验证与修复、证据驱动 review/export、轨迹评估、provider 诊断、
  telemetry 状态和 Python/Node JSONL SDK。
- 人类 REPL 与严格 JSON/JSONL 接口共用安全契约；进度、错误、退出码及审计
  元数据保持可见。

## 文档

- [示例](docs/demo-script.md)
- [更新日志](docs/CHANGELOG.md)

当前版本：`v0.7.0`。许可证：MIT。
