ForgeCode（自研、可审计的本地 coding agent）
仓库：https://github.com/onlyslime/ForgeCode
安装与运行（Windows PowerShell）：
1. 安装 uv：执行 `irm https://astral.sh/uv/install.ps1 | iex`。
2. 安装本项目，二选一：
   方法一（GitHub）：执行
   `uv tool install "git+https://github.com/onlyslime/ForgeCode.git"`，再运行
   `uv tool update-shell`；重开 PowerShell 后可直接使用 `fcc`。
   方法二（ZIP）：解压仓库并进入目录，执行 `uv sync`，然后用
   `uv run forgecode doctor` 检查环境、用 `uv run forgecode` 启动。
   ZIP 后若需直接 `fcc`，执行 `uv tool install --editable .`、
   `uv tool update-shell`，再重开 PowerShell。

在线运行时在 `fcc` 输入 `/login`，填写 URL、模型 ID 和 API key；凭据只保存在本地。

安全提示：Plan 可在未信任目录中只读检查。Act/Bypass 前在终端执行
`fcc trust status`，再用 `fcc trust grant` 授予信任；撤销用 `fcc trust revoke`。
`/trust` 不是交互命令；Bypass、`--auto-approve` 仅用于可信或临时目录。

离线演示：`uv run forgecode --workspace <临时目录> run --demo --auto-approve`。

特色：自研 AgentLoop、tool calling、多家 provider、离线模式；Plan/Act/Bypass、WorkspaceGuard、审批审计、上下文、review、JSON/JSONL、RPC、Python/Node SDK。

限制：WorkspaceGuard 不是操作系统沙箱。详见 README.md、README.zh.md、docs/。
