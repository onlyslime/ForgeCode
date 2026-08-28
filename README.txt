项目：ForgeCode（自研、可审计的本地 coding agent）

Git 仓库：https://github.com/onlyslime/ForgeCode（private；公开需所有者决定）

运行：Python 3.11+、uv。在全新目录执行：`uv sync`；`uv run forgecode doctor`；
`uv run pytest`。离线演示：`uv run forgecode --workspace <目录> run --demo --auto-approve`，
可加 `--demo-task json`。在线模型只从环境变量 FORGECODE_API_KEY、FORGECODE_BASE_URL、
FORGECODE_MODEL 读取，仓库不含凭据。

特色：自研协议、tool calling、AgentLoop、错误回传与有限修复；
Plan/Act、WorkspaceGuard、审批、命令风险/超时/脱敏；AGENTS.md 规则、@文件/Git 上下文、
结构化计划；安全 patch、真实测试、JSONL 审计、checkpoint、resume/fork；持久事务、
内容寻址备份、hash 冲突保护撤销；严格 TOML 测试 profile；review 聚合 session/plan/context/
transaction/test/hook/diff 并支持 digest export/verify；取消、重试、unresolved
恢复；v0.0.9 自动滚动上下文压缩（sequence/fingerprint）、整条轨迹 `eval` 评分、`context
complete` 路径建议、`config profiles`/`/model` 切换审计、session tree/clone/import（不重放副作用）。

Plan 不执行副作用，断流不执行工具；恢复/撤销冲突返回 3，取消返回 130。压缩和备份在
ignored `.forgecode/`。审批是启发式防线，不是操作系统沙箱；暂不含 IDE、浏览器、
云执行、远程 MCP、worktree、并行子代理和自动 push。Self Forcing 仅作真实 rollout、整体
轨迹评估和滚动有界状态的方法论启发，并非视频模型复现。
