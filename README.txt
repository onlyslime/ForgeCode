ForgeCode（自研、可审计的本地 coding agent）

仓库：https://github.com/onlyslime/ForgeCode
版本：v0.8.27

环境：Python 3.11+、uv

安装与运行（Windows PowerShell，二选一）：
方法1（GitHub）：`irm https://astral.sh/uv/install.ps1 | iex`，然后执行
`uv tool install "git+https://github.com/onlyslime/ForgeCode.git"`、
`uv tool update-shell`，重新打开 PowerShell 后运行 `fcc`。
方法2（ZIP）：在 GitHub 点 Code→Download ZIP，解压进入 ForgeCode 目录，执行
`irm https://astral.sh/uv/install.ps1 | iex`、`uv sync`、`uv run forgecode doctor`，
再用 `uv run forgecode` 启动。需全局命令时执行 `uv tool install --editable .`，
运行 `uv tool update-shell` 后重开 PowerShell。

在线运行时在 `fcc` 输入 `/login`，填写 URL、模型 ID 和 API key。凭据只保存在本地。

离线演示：
`uv run forgecode --workspace <临时目录> run --demo --auto-approve`
演示完成读取、失败测试、补丁、修复和验证，并记录审计证据。请使用新的临时目录。

特色：自研 AgentLoop、统一 tool calling、多家 provider、离线模式；Plan/Act/Bypass、WorkspaceGuard、原子 patch、审批/超时/取消、测试修复、session、撤销、上下文、review、JSON/JSONL、RPC、Python/Node SDK。

限制：WorkspaceGuard 不是操作系统沙箱；Bypass、`--auto-approve` 只应在可信或临时工作区使用。详见 README.md、README.zh.md、docs/。
