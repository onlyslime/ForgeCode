ForgeCode（自研、可审计的本地 coding agent）

仓库：https://github.com/onlyslime/ForgeCode
版本：v1.0.0

环境：Python 3.11+、uv

安装与运行（Windows PowerShell）：
GitHub 安装：执行 `irm https://astral.sh/uv/install.ps1 | iex`，再运行
`uv tool install "git+https://github.com/onlyslime/ForgeCode.git"`、
`uv tool update-shell`，重开 PowerShell 后运行 `fcc`。
ZIP 安装：解压仓库后运行 `uv sync`、`uv run forgecode doctor`、`uv run forgecode`。

在线运行时在 `fcc` 输入 `/login`，填写 URL、模型 ID 和 API key。凭据只保存在本地。

安全提示：Plan 模式可在未信任工作区中只读检查。使用 Act/Bypass 前，在终端执行
`fcc trust status` 查看状态，执行 `fcc trust grant` 授予当前工作区信任；需要时用
`fcc trust revoke` 撤销。`/trust` 不是交互会话命令。Bypass 和 `--auto-approve` 只应在可信或临时目录使用。

离线演示：`uv run forgecode --workspace <临时目录> run --demo --auto-approve`；
请使用新的临时目录。

特色：自研 AgentLoop、统一 tool calling、多家 provider、离线模式；Plan/Act/Bypass、WorkspaceGuard、原子 patch、审批/超时/取消、测试修复、session、撤销、上下文、review、JSON/JSONL、RPC、Python/Node SDK。

限制：WorkspaceGuard 不是操作系统沙箱。详见 README.md、README.zh.md、docs/。
