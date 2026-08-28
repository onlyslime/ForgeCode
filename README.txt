项目：ForgeCode（自研、可审计的本地 coding agent）

Git 仓库：https://github.com/onlyslime/ForgeCode（private）

如何运行：Python 3.11+ 与 uv；根目录执行 `uv sync`、
`uv run forgecode doctor`、`uv run forgecode tools`、`uv run pytest`。
离线演示用全新目录：`uv run forgecode --workspace <目录> run --demo
--auto-approve`（可加 `--demo-task json`）。在线模型凭据只从
`FORGECODE_API_KEY`、`FORGECODE_BASE_URL`、`FORGECODE_MODEL` 读取。

特色功能：自研 provider-neutral 协议、tool calling、AgentLoop、错误回传与有限
修复；Plan/Act 权限、WorkspaceGuard、命令风险/超时/脱敏；AGENTS.md 规则、
@文件/目录/Git 上下文、结构化计划和脚本化交互；安全 patch、真实测试、JSONL
审计、checkpoint、压缩、resume/fork；持久 transaction ledger、内容寻址备份、
hash 冲突保护的跨进程撤销；严格 TOML 测试 profile（argv、环境白名单、阶段、
额度、超时、期望退出码）；review 聚合 session/plan/context/transaction/test/
hook/diff，执行 secrets、路径、可疑命令、Python AST 检查并支持 digest
export/verify；取消 token、deadline、provider retry、unresolved 恢复证据；
`--jsonl` 单行 envelope，stdout 不混入进度或审批提示。

版本：v0.0.8。Plan 不执行副作用，断流不执行工具；恢复/撤销冲突返回 3，取消
返回 130。记录和备份留在 ignored `.forgecode/`。审批是启发式边界而非操作系统
沙箱；暂不包含 IDE、浏览器控制、云执行和多代理。
