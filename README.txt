项目：ForgeCode（自研、可审计的本地 coding agent）

仓库：https://github.com/onlyslime/ForgeCode

环境：Python 3.11+、uv。安装后运行：
`uv sync`、`uv run forgecode doctor`、`uv run pytest`。

离线运行：
`uv run forgecode --workspace <临时目录> run --demo --auto-approve`。
在线运行：执行 `uv run forgecode chat`（交互别名为 `fcc`），输入 `/login`，
按提示填写 URL、ID、KEY。

特色：自研 AgentLoop、模型 tool calling、Plan/Act/Bypass、安全文件读写与
apply_patch、命令风险/审批/超时/取消、真实测试与有限修复、事务撤销、规则与
上下文压缩、checkpoint/session/review/eval、JSONL RPC、Node/Python SDK。
离线 demo 会真实展示读取、失败、修改、测试和验证闭环。

当前版本：v0.6.2。
