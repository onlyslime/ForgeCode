项目：ForgeCode（自研、可审计 coding agent）

仓库：https://github.com/onlyslime/ForgeCode（当前 private，公开需所有者决定）

运行：Python 3.11+、uv。执行 `uv sync`、`uv run forgecode doctor`、
`uv run pytest`。离线演示：`uv run forgecode --workspace <目录> run --demo --auto-approve`，
可加 `--demo-task json`。在线模型仅从 `FORGECODE_API_KEY`、
`FORGECODE_BASE_URL`、`FORGECODE_MODEL` 读取，仓库不含凭据。

特色：自研 AgentLoop、模型 tool calling、Plan/Act、WorkspaceGuard、审批、
风险/超时/取消/恢复、凭据脱敏；安全文件 patch、真实测试、事务备份与撤销；
规则与仓库上下文、上下文压缩、checkpoint/resume/fork、review/eval；
JSONL RPC、Node/Python SDK、session list/tree、profile 审计和 telemetry/offline 策略。
所有入口共享同一工具、审批、日志和安全边界；JSONL stdout 是稳定 envelope，
进度与审批写 stderr。`sessions --state` 可筛选后台会话，workspace 参数会规范化校验。

当前版本 v0.0.35。安全信任通过 `trust status|grant|revoke` 管理，凭据通过环境变量引用。

非目标：IDE、Web/桌面 UI、浏览器、远程 MCP、插件市场、worktree、并行子代理、
云执行和操作系统级 sandbox；审批与风险分类是防线，但不替代 OS 隔离。
