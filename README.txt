项目：ForgeCode（自研、可审计的本地 coding agent）

仓库：https://github.com/onlyslime/ForgeCode（当前 private，公开需所有者决定）

运行：Python 3.11+、uv。全新目录执行 `uv sync`、`uv run forgecode doctor`、
`uv run pytest`。离线演示：`uv run forgecode --workspace <目录> run --demo --auto-approve`，
可加 `--demo-task json`。在线模型只从 `FORGECODE_API_KEY`、`FORGECODE_BASE_URL`、
`FORGECODE_MODEL` 读取，仓库不含凭据。

特色：自研 provider-neutral 协议、AgentLoop、tool calling、错误回传和有限修复；
Plan/Act 权限边界、WorkspaceGuard、审批、命令风险/超时/脱敏；AGENTS.md 规则、
@文件/Git 上下文、安全 patch、真实测试、事务备份与 hash 冲突撤销；JSONL 审计、
checkpoint/resume/fork、取消/重试/unresolved 恢复、review/eval、上下文自动压缩、
路径建议、profile/模型审计和 session tree/clone/import（不重放副作用）。

v0.0.10 增加单一交互 worker：有界 FIFO follow-up、`/pause`、`/resume`、`/cancel`，
暂停恢复校验 checkpoint/规则/计划/配置指纹，运行中切换模型会拒绝；`chat --jsonl`
stdout 每行是 envelope，进度/审批只去 stderr。Plan 不执行副作用，取消或未决不会误报成功。

暂不含 IDE、浏览器、云执行、远程 MCP、worktree、并行子代理、后台调度、自动 push 或
操作系统级 sandbox。审批和风险分类是防线，不是 OS 隔离。
