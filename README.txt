项目：ForgeCode（自研、可审计的本地 coding agent）

仓库：https://github.com/onlyslime/ForgeCode

运行：Python 3.11+、uv；根目录执行 `uv sync`、`uv run forgecode doctor`、
`uv run forgecode tools`、`uv run pytest`。离线演示用全新目录：
`uv run forgecode --workspace <目录> run --demo --auto-approve`，或加
`--demo-task json`。在线模型只从 FORGECODE_API_KEY、FORGECODE_BASE_URL、
FORGECODE_MODEL 环境变量读取凭据。

特色：自研 provider-neutral 协议、tool calling、AgentLoop、上下文预算、错误
回传和有限修复；Plan/Act 双层权限与 WorkspaceGuard 路径保护；unified/
Begin Patch 多文件预验证、diff 预览、审批、事务 id、哈希冲突、原子替换和进程内
回滚；命令风险分类、危险硬拦截、超时、进程终止、输出限制和脱敏；repository
map；带 schema_version/run_id/sequence 的 JSONL 事件、checkpoint、session
show/export、status/diff、安全 resume dry-run。Plan 不执行副作用，恢复冲突返回
退出码 3，不会自动重放写入或命令。

版本 v0.0.5；测试全部通过，Windows 无符号链接权限时跳过对应测试。风险识别是
启发式审批边界，不是操作系统沙箱；掉电/磁盘损坏时无法保证跨文件原子性。暂不
包含 IDE、浏览器、MCP marketplace、云执行和多代理。
