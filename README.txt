项目：ForgeCode（自研可审计 coding agent）

仓库：https://github.com/onlyslime/ForgeCode（当前 private，公开需所有者决定）

运行：Python 3.11+、uv。全新目录执行 `uv sync`、`uv run forgecode doctor`、
`uv run pytest`。离线演示：`uv run forgecode --workspace <目录> run --demo --auto-approve`，
可加 `--demo-task json`。在线模型只从 `FORGECODE_API_KEY`、`FORGECODE_BASE_URL`、
`FORGECODE_MODEL` 读取，仓库不含凭据。

特色：自研协议、AgentLoop、tool calling、有限修复；
Plan/Act、WorkspaceGuard、审批、风险/超时/脱敏；AGENTS.md、@文件/Git 上下文、
安全 patch、真实测试、事务备份/hash 撤销；JSONL 审计、checkpoint/resume/fork、
取消/恢复、review/eval、上下文压缩、profile 审计和 session tree（不重放副作用）。

v0.0.10 增加单一交互 worker：有界 FIFO follow-up、`/pause`、`/resume`、`/cancel`；
v0.0.11 增加 Pi-inspired `!<command>`/`!!<command>`：单 `!` 结果进入模型，双 `!!` 仅用户/审计可见；
v0.0.12 增加 `chat/start/run` 的 `--tools`、`--exclude-tools`、`--no-tools`，配置与 CLI 只做单调收窄，
均复用审批、风险、超时、取消和脱敏边界，暂停恢复校验 checkpoint/规则/计划/配置指纹；
stdout 每行是 envelope，进度/审批只去 stderr。Plan 不执行副作用，取消或未决不会误报成功。

暂不含 IDE、浏览器、云执行、远程 MCP、worktree、并行子代理、后台调度、自动 push 或
操作系统级 sandbox。审批和风险分类是防线，不是 OS 隔离。
