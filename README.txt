项目：ForgeCode（自研、可审计的本地 coding agent）

仓库：https://github.com/onlyslime/ForgeCode

运行：Python 3.11+、uv；根目录执行 `uv sync`、`uv run forgecode doctor`、
`uv run forgecode tools`、`uv run pytest`。离线演示用全新目录：
`uv run forgecode --workspace <目录> run --demo --auto-approve`，或加
`--demo-task json`。在线模型只从 FORGECODE_API_KEY、FORGECODE_BASE_URL、
FORGECODE_MODEL 环境变量读取凭据。

特色：自研 provider-neutral 协议、tool calling、AgentLoop、错误回传与有限修复；
Plan/Act 双层权限、WorkspaceGuard、命令风险/超时/脱敏；作用域 AGENTS.md 规则、
@文件/目录/Git 精确上下文、结构化计划和含 /plan、/test、/review、/compact、/undo
的脚本化交互；安全 patch、真实测试、JSONL 审计、checkpoint、上下文压缩与
resume/fork；持久事务 ledger、ignored 原始备份和 hash 冲突保护的跨进程撤销；
typed TOML profile 与安全 SSE tool-call 组装。Plan 不执行副作用，断流不执行工具，
恢复/撤销冲突返回退出码 3且不会覆盖外部编辑。

版本 v0.0.7；离线测试、索引和技能功能均可按 README 命令验证。跳过项均因当前 Windows 进程无符号链接创建权限。
本项目是启发式审批边界而非操作系统沙箱；暂不包含 IDE、浏览器、云执行和多代理。
