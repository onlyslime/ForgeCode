项目名称：ForgeCode（自研本地 coding agent）

Git 仓库地址：https://github.com/onlyslime/ForgeCode

如何运行：
1. 安装 Python 3.11+ 与 uv，在项目根目录执行 uv sync。
2. 执行 uv run forgecode doctor 检查环境，uv run forgecode tools 查看工具，uv run pytest 运行测试。
3. 离线演示请创建新的临时目录后执行：
   uv run forgecode --workspace <临时目录> run --demo --auto-approve
   演示会读取有缺陷的 calculator，运行失败测试，经审批应用 patch，再运行测试通过。
4. 真实模型需在环境变量设置 FORGECODE_API_KEY、FORGECODE_BASE_URL、FORGECODE_MODEL，再执行 forgecode run "任务"；默认逐次询问写入和命令审批。

特色功能：
- 自行实现 provider-neutral 协议、结构化 tool calling、AgentLoop、上下文预算、错误回传、有限修复和验证，不依赖现成 agent 框架或 SDK。
- Plan/Act 权限边界：plan 只读且工具执行层拒绝副作用；act 支持审批后的写入、命令和 apply_patch。
- apply_patch 支持 unified diff、*** Begin Patch、多文件/多 hunk、新建和显式删除，预验证、路径保护、diff 预览、原子写入和失败恢复。
- WorkspaceGuard 防止路径和符号链接逃逸；命令有风险分类、硬拒绝、超时、输出限制、进程终止和敏感环境变量清理。
- workspace summary 提供语言、构建文件、测试目录和 Git 状态；SessionStore 以脱敏、有上限的 JSONL 记录模式、工具、审批、结果和验证。

补充说明：项目面向题目的单智能体本地 MVP，演示不需要网络或 API key。命令风险识别是保守启发式策略，不等同于操作系统沙箱；暂不包含 IDE、浏览器、MCP、云执行和多代理。当前发布版本为 v0.0.4。
