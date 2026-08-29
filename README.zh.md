# ForgeCode

> 一个从协议开始自己造的本地 coding agent。

ForgeCode 是一个简洁、可审计的终端工具。它自己处理模型消息、工具、AgentLoop、工作区、审批、会话记录和验证流程；代码保持开放、直接、方便改造。

## 安装与开始

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync
uv run forgecode doctor
uv run forgecode chat
```

没有 Python 或 uv 时，可安装带独立运行文件的 npm 包（当前先提供 Windows x64）：

```powershell
npm install -g @onlyslime/forgecode
fcc
```

配置模型时，在 `fcc` 中输入 `/login`，填写服务商提供的 URL、ID 和 KEY：

```powershell
fcc --act
/login
# URL、ID、KEY
```

## 它能做什么

你提出任务，ForgeCode 会查看相关文件，调用工具，修改代码，运行测试，处理失败，并给出有证据的结果。

- 列文件、读取、搜索、写入和 unified `apply_patch`
- Plan、Act、Bypass 三种工作模式
- 带超时、取消和输出限制的命令与测试执行
- 规则、引用、仓库上下文和上下文压缩
- session、checkpoint、事务撤销、review、eval 和 JSONL 审计
- skills、hooks、Node/Python SDK 和 JSONL RPC
- 固定输入栏、多行粘贴、实时进度和 Esc 取消

## 交互命令

在 `fcc` 中输入 `/help` 查看全部命令。常用命令包括 `/mode`、`/tools`、`/files`、`/skills`、`/rules`、`/tree`、`/review`、`/compact`、`/cancel`、`/quit` 和 `/exit`。

## 文档

- [架构](docs/architecture.md)
- [示例](docs/demo-script.md)
- [能力清单](docs/implemented-features.md)
- [更新日志](docs/CHANGELOG.md)

当前版本：`v0.6.3`。许可证：MIT。
