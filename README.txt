项目：ForgeCode（自研、可审计的本地 coding agent）

仓库：https://github.com/onlyslime/ForgeCode

环境：Python 3.11+、uv。

空白 Windows 电脑：在 PowerShell 执行
`irm https://astral.sh/uv/install.ps1 | iex`，重新打开 PowerShell 后执行
`uv tool install "git+https://github.com/onlyslime/ForgeCode.git"` 和
`uv tool update-shell`。再次打开 PowerShell，即可在任意目录运行 `fcc`。

已下载 GitHub ZIP：解压后进入项目目录，执行 `uv sync`，再用
`uv run forgecode doctor` 检查环境，使用 `uv run fcc` 启动。若希望在
任意目录直接输入 `fcc`，在该目录执行 `uv tool install --editable .`，
然后 `uv tool update-shell` 并重新打开 PowerShell。

在线运行时输入 `/login`，按提示填写 URL、ID、KEY。

离线运行：`uv run forgecode --workspace <临时目录> run --demo --auto-approve`。

特色：自研 AgentLoop、模型 tool calling、Plan/Act/Bypass、安全文件读写与
apply_patch、命令风险/审批/超时/取消、真实测试与有限修复、事务撤销、规则与
上下文压缩、checkpoint/session/review/eval、JSONL RPC、Node/Python SDK。
离线 demo 会真实展示读取、失败、修改、测试和验证闭环。

当前版本：v0.7.0。
