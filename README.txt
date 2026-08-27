项目名称：ForgeCode（自研本地 coding agent）

Git 仓库地址：https://github.com/onlyslime/ForgeCode（私有仓库）
当前版本：v0.0.3

如何运行：
1. 安装 Python 3.11+ 和 uv，在项目根目录执行 uv sync。
2. 执行 uv run forgecode doctor 检查环境，uv run forgecode tools 查看工具。
3. 执行 uv run forgecode --workspace . run --demo --auto-approve，运行离线演示；执行 uv run pytest 运行测试。
4. 使用真实模型时，在环境变量设置 FORGECODE_API_KEY、FORGECODE_BASE_URL、FORGECODE_MODEL，再运行 forgecode run "任务"。默认会在写文件和执行命令前请求批准。

特色功能：
- 自行实现 OpenAI-compatible 模型适配器、结构化 tool calling、AgentLoop、上下文预算和终止条件，不依赖任何现成 agent 框架或 SDK。
- 内置列文件、读文件、literal/regex 搜索、审批写文件、审批命令工具；命令保留 stdout、stderr、退出码并支持超时。
- WorkspaceGuard 防止相对路径、绝对路径和符号链接逃逸；SessionStore 记录脱敏 JSONL 事件；失败结果会回传模型并支持有限修复和验证。
- DemoProvider 可离线演示读取、创建文件、故意失败、错误回传、修复和验证；CLI 输出审批、结果、停止原因与 git diff 摘要。

说明：项目面向考核题目的本地单智能体 MVP，暂不包含 IDE、浏览器、MCP、云执行和多代理。运行时凭据只通过环境变量提供，绝不提交到仓库。
