项目名称：ForgeCode（自研 coding agent 框架）

Git 仓库：https://github.com/onlyslime/ForgeCode（私有仓库）
当前版本：v0.0.2

运行方式：
1. 安装 Python 3.11+ 和 uv。
2. 在项目根目录执行 uv sync。
3. 执行 uv run forgecode doctor 检查环境。
4. 执行 uv run forgecode tools 查看工具。
5. 执行 uv run pytest 运行测试。

特色功能：
- 自行实现 AgentLoop、模型适配器接口、ToolRegistry 和 JSONL 会话存储，不依赖现成 agent 框架。
- 内置文件列表、文件读取、正则搜索、文件写入和命令执行工具。
- WorkspaceGuard 限制路径必须位于工作区内；写入和命令执行需要审批，命令有超时并保留退出码、标准输出和错误输出。
- 代码与测试分离，后续可接入 OpenAI 兼容接口或其他模型供应商。

说明：本版本是可测试的框架骨架，下一阶段接入真实模型、结构化 tool calling、上下文预算和交互式任务命令。API key 只放环境变量或本地未入库配置文件，不进入仓库。
