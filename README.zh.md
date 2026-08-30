# ForgeCode

> 一个能检查、修改、验证并解释工作的本地 coding agent。

[English](README.md) · [更新日志](docs/CHANGELOG.md) · [示例](docs/demo-script.md)

ForgeCode 是一个自建、可审计的 coding agent，面向真实的软件开发工作。它把自然语言任务转换为透明的模型决策、本地工具调用、文件变更和验证流程。协议、AgentLoop、工具、工作区边界、审批和会话证据都在本仓库中实现，而不是隐藏在某个 agent SDK 后面。

`本地优先` · `工具调用` · `流式输出` · `工作区安全` · `会话审计`

<p align="center">
  <img src="show/introduce.gif" alt="ForgeCode 交互式终端演示" width="900">
</p>

## 一分钟开始

在 Windows PowerShell 中任选一种安装方式。

### 方法 1：从 GitHub 安装

```powershell
irm https://astral.sh/uv/install.ps1 | iex
uv tool install "git+https://github.com/onlyslime/ForgeCode.git"
uv tool update-shell
fcc
```

### 方法 2：下载 GitHub ZIP 安装

在 GitHub 点击 **Code → Download ZIP**，解压后进入解压出的 `ForgeCode` 目录，打开 PowerShell：

```powershell
irm https://astral.sh/uv/install.ps1 | iex
uv sync
uv run forgecode doctor
uv run forgecode
```

如需在任意目录使用 `fcc`，执行 `uv tool install --editable .`，再执行
`uv tool update-shell` 并重新打开 PowerShell。

在聊天中使用 `/login` 输入服务商 URL、模型 ID 和 API key。凭据只保存在本地，不会进入仓库。

## 一次真实运行

给 ForgeCode 一个普通的软件工程任务，例如：

```text
阅读这个 Python 项目，找出失败的边界情况，修复实现，
添加回归测试，并运行测试套件。
```

交互过程是透明的，而不是黑盒：

```text
◆ assistant
我会检查项目结构和现有测试。

▸ 读取 src/calculator.py
✓ 读取 · 42 行
▸ 搜索 "divide"
✓ 搜索 · 4 个匹配

◆ assistant
我找到了边界情况，现在添加回归测试。

▸ 应用补丁
  - 旧行为
  + 修正后的行为
✓ 应用补丁
▸ 运行测试
✓ 运行 · exit 0

完成 · 验证通过 · 用时 18.4 秒 · 4 个工具步骤
```

## 它能做什么

| 领域 | 能力 |
| --- | --- |
| 理解 | 列出和读取文件、文本/正则搜索、仓库概览、符号、定义、引用、元数据 |
| 修改 | 创建文件、原子写入、统一补丁、红绿预览、事务记录 |
| 验证 | 测试、诊断、有界 shell 命令、标准输出/错误、退出码、修复尝试 |
| 控制 | Plan、Act、Bypass、暂停、恢复、取消/Esc、安全边界 steering、后续任务队列、实时生命周期/计时状态 |
| 上下文 | `AGENTS.md` 规则、显式引用、有界用户记忆、增量索引、上下文搜索、压缩、健康诊断 |
| Git | status、diff、log、worktree、review、撤销和恢复检查 |
| 进程 | 后台命令、状态轮询、输出限制、安全终止 |
| 自动化 | JSON、JSONL、RPC、Python 嵌入 API、Node JSONL 客户端 |

## 边界如何工作

```text
用户提示
    ↓
AgentLoop + 服务商适配器
    ↓
经过校验的统一工具调用
    ↓
ToolRegistry
    ↓
WorkspaceGuard + 模式 + 风险 + 审批
    ↓
本地文件、命令、测试
    ↓
会话事件、审计、验证
```

模型只提出动作，真正执行由本地代码完成。每个路径都会经过工作区校验。写入和命令执行有边界、按策略审批、可取消，并记录结果。Plan 是只读模式，Act 允许经过批准的副作用，Bypass 需要明确选择信任工作区。WorkspaceGuard 是应用层边界，不是操作系统沙箱。

## 常用命令

在 `fcc` 中可以从 `/help`、`/tools`、`/status`、`/files`、`/rules`、`/tree`、`/review`、`/context`、`/compact`、`/events`、`/steer`、`/memory`、`/cancel` 和 `/exit` 开始。`/steer <消息>` 会在下一次模型请求前引导正在运行的任务，不会打断工具副作用。使用 `!command` 将有界命令结果发送给模型，使用 `!!command` 则只在本地执行。跨会话记忆由用户显式管理：`forgecode memory add/show/remove/clear` 或交互式 `/memory`，并作为不可信上下文注入。

```powershell
fcc --print "review this project" --jsonl
fcc --jsonl
```

## 文档

- [英文文档](README.md)
- [示例脚本](docs/demo-script.md)
- [更新日志](docs/CHANGELOG.md)
- [文档导航](docs/README.md)
- [架构说明](docs/architecture.md)
- [已实现能力](docs/implemented-features.md)

## 仓库结构

```text
src/forgecode/   协议、AgentLoop、服务商、工具、安全、存储、CLI
tests/            确定性的回归测试套件
docs/             架构说明、示例、研究资料和历史记录
sdk/node/         简洁的 JSONL 客户端
```

MIT 许可证。当前版本：`v0.8.27`。
