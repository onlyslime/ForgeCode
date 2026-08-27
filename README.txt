ForgeCode 是为考核自研的 Python coding agent 框架，使用 uv 管理环境，不依赖现成 agent SDK。

当前版本：v0.0.1。已建立 CLI、工作区路径保护、文件/搜索/命令工具接口、JSONL 会话存储、AgentLoop 和模型适配器接口，以及自动化测试。

运行：
1. 安装 uv。
2. 执行 uv sync。
3. 执行 uv run forgecode doctor 检查环境。
4. 执行 uv run pytest 运行测试。

目录：src/forgecode 为源码，tests 为测试，docs/assignment 为题目材料，docs/research 为调研资料。API key 只能放环境变量或未入库的 .env 文件。

版本规则：每次提交使用 vA.B.C。未得到明确指令前只递增 C；A 更新时 B、C 从 0 开始，B 更新时 C 从 0 开始。
