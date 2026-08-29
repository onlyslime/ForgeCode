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

解压 ZIP，在解压后的项目目录打开 PowerShell：

```powershell
uv sync
uv run forgecode doctor
uv run fcc
```

`uv sync` 只负责同步当前项目环境，不会注册全局命令。如果希望在任意目录
直接使用 `fcc`，在项目目录执行一次：

```powershell
uv tool install --editable .
uv tool update-shell
```

重新打开 PowerShell 即可。由于这是 editable 安装，请保留项目目录。

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
