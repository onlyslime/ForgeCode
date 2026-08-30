# 项目名称  
AI 编码助手（虚拟开发助理）项目研究

## 执行摘要  
本报告针对构建智能编码辅助系统进行了深入调研与规划。前半部分保留了对 Earendil Pi、DeepSeek Harness、OpenAI Codex、Anthropic Claude Code 和 OpenCode 的背景比较；补充部分进一步覆盖 16 个代表性产品（终端代理、IDE 代理、补全助手和云端软件工程师），以官方文档逐条记录文件、搜索、命令、测试、计划、权限、Git、扩展和治理能力。在此基础上，我们抽象出 F01-F28 能力原子，区分本题必须自研的 P0 闭环、建议实现的 P1 和后置的 P2，并给出不依赖现成 agent 框架的实现方案、验收场景和截止日前执行顺序。  

## 产品比较  
下表比较了各 AI 编码代理产品的关键属性：功能特性、API/集成方式、部署方式、定价许可、优缺点等。



| 产品 | 功能特性 | API/集成方式 | 部署方式 | 定价/许可 | 优缺点 |
|---|---|---|---|---|---|
| **Pi (Earendil)** | 轻量级终端编码代理；支持多模型提供商切换、上下文紧凑管理、会话树等 | 命令行交互；支持Print/JSON模式、RPC接口和Node SDK | 基于Node.js的CLI工具；可在本地终端运行（支持容器化） | 开源免费（MIT 许可证） | 优：开源免费、可高度定制，支持多模型。缺：不含内置规划/子代理，需自行编写扩展功能；主要面向终端用户。 |
| **DSH (DeepSeek Harness)** | 全功能插件化代理框架；采用Cordis插件系统，功能模块（模型、工具、UI等）均以插件形式实现。支持标准、代码、简洁、开发者等多种运行模式。内置可安装 Codex/ClaudeCode 子代理 | 提供Web UI（默认地址 http://127.0.0.1:3080）和CLI（如 `npx @deepseek-ai/dsh web`）等接口 | 基于 Node.js 环境；可本地运行并通过Web界面或CLI操作；采用插件形式扩展 | 开源免费（MIT 许可证） | 优：架构灵活、插件化高度可配置，支持子代理和多模式；日志可追踪每次运行。缺：目前为开发者预览版、功能仍在演进；配置复杂、学习曲线较陡。 |
| **OpenAI Codex** | 强大的编码智能代理；内置于ChatGPT生态，利用 GPT-5 系列模型执行端到端任务。具备并行多代理工作流、自动化技能包（Skills）和后台计划任务功能。提供代码审查界面和工作树机制以避免合并冲突。 | 集成于 ChatGPT 客户端（窗口/网页）；提供CLI工具和SDK，并支持Slack、VSCode 等多种集成 | 完全托管云服务；用户通过ChatGPT订阅或CLI/IDE插件使用，无需本地部署 | 商业付费产品；包含在ChatGPT商业/教育/企业套餐中，按使用令牌计费；高阶功能需ChatGPT Plus/Enterprise订阅 | 优：功能最为全面、模型性能领先，适合复杂企业级任务；生态完善。缺：闭源付费，使用成本高，数据需要依赖云端服务存储与处理。 |
| **Claude Code (Anthropic)** | 代理式编码助手；深度读取整个代码仓库，支持多文件、多工具协同工作。支持并行子代理、跨会话协作和动态工作流等高级特性。多平台支持：命令行CLI、桌面App、网页和IDE插件（VS Code、JetBrains）、Slack集成等。 | 提供命令行、桌面/浏览器应用、IDE扩展以及Slack机器人等接口 | 需依赖Anthropic云端服务；用户注册Anthropic账号并付费订阅后使用，部分本地模式支持第三方LLM | 付费订阅模式。基础Pro计划含Claude Code（$17/月起），Max计划提供更高调用额度（$100–200/月）；按API令牌消耗计费。 | 优：功能完备且集成度高，支持丰富的并行与自动化流程；提供企业级管理工具。缺：闭源、需联网使用，成本较高；对数据隐私和合规有一定要求。 |
| **OpenCode** | 开源AI编码代理（Anomaly出品）；支持连接任意LLM（OpenAI、Claude、Gemini等）。提供终端CLI、桌面应用和IDE插件，多会话并行执行能力。内置两种默认“Agent”模式：Build（完全读写模式）和Plan（只读分析模式）。强调隐私，不存储代码或上下文；支持使用GitHub Copilot或ChatGPT账户。 | 提供终端界面和IDE插件，与多个模型供应商API集成（可登录OpenAI/Copilot等账户） | 基于Node.js跨平台工具；可本地安装或打包为桌面应用 | 开源免费（MIT 许可证），提供付费企业版及“Zen”模型优化服务 | 优：完全开源免费、支持多模型，隐私保护好；社区活跃。缺：初期功能依赖社区插件，企业支持较弱；相对其他产品可能在高级特性上较新，优化空间大。 |

## 各产品功能与架构要素分析  
**Pi** – Pi 是 Earendil 公司的开源终端编码代理基座。其设计理念是保持核心极简，并通过插件/模板扩展功能。Pi 支持切换多种大模型提供商（如OpenAI、Anthropic、Google等），可通过命令行交互并以树结构记录历史会话。其核心功能包括上下文自动紧凑、可配置的提示模板和任务技能（skills），可实现在终端中自动执行Shell命令、编辑文件、查询网络等。Pi 自身省略了计划模式和子代理等复杂功能，依赖于用户安装扩展或编写Prompt来增强。Pi 提供交互模式、打印/JSON模式（用于脚本化）、RPC和Node SDK等多种集成方式。由于采用 MIT 许可证开源发布，使用无成本，可自由部署，但也意味着需要开发者自己配置和管理依赖。Pi 适合习惯终端工作流、需要高度定制的场景；不适合追求开箱即用的复杂编码项目。  

**DeepSeek Harness (DSH)** – DSH 是 DeepSeek AI 开源的多功能编码代理框架。其创新点是“**一切皆插件**”：系统使用 Cordis 插件系统将所有功能（模型、工具、界面、会话管理等）模块化。DSH 预置了多个运行模式：标准(Standard)模式包含完整开发工具集（文件编辑、Shell、网络搜索、计划任务、子代理和工作流等）；代码(Code)模式允许模型生成的代码逐步执行复杂操作；极简(Minimal)模式仅保留基本的Shell和编辑器以作基线测试；创作者(Creator)模式则用于检查运行时状态、调试插件、创建预设模式。DSH 提供Web UI和CLI接口，可通过 `npx @deepseek-ai/dsh web` 启动（默认访问 http://127.0.0.1:3080）。在最新预览版（rc.8）中，Codex 和 Claude Code 已作为可按需安装的 Profile 插件提供。DSH 记录每次运行的完整轨迹日志，可重放和搜索，有利于调试和审计。该项目目前处于开发者预览阶段，开源发布（MIT），高度可扩展但配置复杂，需要较强的架构和编程能力来定制插件。DSH 适用于需要构建自定义 AI 代理平台或研究框架的团队，不适合寻求稳定商用产品的用户。  

**OpenAI Codex** – Codex 是 OpenAI 面向软件开发的智能编码产品，基于 GPT-5 系列代码模型。它提供类似“智能机器人”式的编码协作环境：支持在ChatGPT客户端、编辑器和终端中使用同一个会话环境，并集成了诸如Slack机器人、IDE插件等多种接入方式。Codex 的核心能力包括自动完成端到端开发任务（新特性开发、重构、测试生成、代码审查等），引入了“技能”(Skills)功能，使用户可以将一组指令和脚本打包成可重复调用的插件。它支持长程上下文（号称最高400万Token窗口），并通过工作树（worktrees）机制管理并发分支以避免冲突。Codex 应用内置专门的代码审查界面和自动化功能，用户可设定周期性任务在后台运行。在企业环境中，Codex 提供审计和安全控制功能，与现有安全策略相集成。产品定价包括在 ChatGPT 付费订阅（商业版/教育版/企业版）中，按使用的API令牌计费（Plus/Pro用户也可获得基础接入）。Codex 优点是技术领先、功能最为齐全，且直接由 OpenAI 支持；缺点是闭源付费、运营成本高、数据需依赖云端服务（可能产生隐私合规顾虑）。  

**Claude Code (Anthropic)** – Claude Code 是Anthropic推出的智能编码助手，定位于“代理式”开发工具。它能完整读取并理解代码仓库，可在终端、桌面/网页App、IDE 插件或 Slack 中直接对项目进行增删改查。Claude Code 支持并行子代理、跨会话消息传递和动态工作流等功能，允许多个任务并发运行而相互隔离（通过Git工作树防止冲突）。其核心功能包括生成新功能、修复缺陷、生成测试、代码审查等，并能够根据团队标准自动执行重复性任务。Claude Code 在客户端提供会话历史、Diff预览和计划审查等功能（例如VS Code插件集成了inline diff和会话历史）。Claude Code 服务按API令牌使用量计费，提供多档订阅计划：基础Pro计划（包含Claude Code）每月约17美元起，高级Max计划提供更大令牌额度（5倍或20倍）每月100–200美元。据官方文档，在企业部署中平均每开发者每活跃日成本约13美元（约150–250美元/月）。优点是功能丰富且与Anthropic生态紧密集成；缺点是闭源付费、需要联网使用（尽管支持第三方模型）、成本较高，企业使用需注意数据隐私和安全合规。  

**OpenCode** – OpenCode 是 Anomaly Innovations 维护的开源AI编码代理，广受社区欢迎。它支持连接市面上超过75个LLM提供商（包括OpenAI、Anthropic、Google等），并可自动配置相应的代码语言服务（LSP）。OpenCode 提供终端CLI界面、桌面应用和IDE插件等多种使用方式。其内置两种Agent角色：**build**（完全访问模式，可读写代码）和 **plan**（只读模式，用于分析或规划）；可以通过切换Agent来选择行为。OpenCode 允许多会话并行运行，还支持通过Web链接分享会话上下文。作为隐私优先的设计，OpenCode 默认不记录或上传用户代码和上下文。用户可登录GitHub Copilot或OpenAI账户使用私有模型，也可利用社区的“Zen”模型集（经优化的高级模型）。由于采用MIT开源许可证，OpenCode免费且跨平台，适合需要本地隐私计算或高度定制的场景；缺点是依赖社区插件生态，企业支持和高级功能尚在发展中。  

## 系统架构与流程  
AI 编码助手系统通常由以下模块组成：**用户界面层**（终端、编辑器或Web界面）、**代理核心引擎**（负责会话管理、任务编排、调用模型和工具）、**上下文/数据存储**（本地代码仓库、临时文件、会话日志等）、**模型服务接口**（调用云端或本地LLM）、以及若干**外部工具**（如Shell、搜索API、LSP等）。下图给出了一个示意架构：用户通过终端或IDE插件提交查询（如“添加函数”），**代理引擎**负责加载相关代码上下文、构造提示并调用 LLM 服务，LLM 返回结果后引擎可能执行文件编辑或命令；所有操作被日志化存储用于后续审计和恢复。  

```mermaid
graph TD
  U[用户（开发者）] -->|输入查询| UI[UI / IDE / CLI]
  UI --> AgentCore[代理核心引擎]
  AgentCore --> Context[上下文管理 (代码仓库、历史会话)]
  AgentCore --> Tools[工具集 (Shell、LSP、搜索等)]
  AgentCore --> LLM[LLM 服务（OpenAI/GPT/Claude/本地模型）]
  Context --> AgentCore
  LLM --> AgentCore
  Tools --> AgentCore
  AgentCore --> DB[(会话/日志存储)]
  AgentCore --> UI
```
*图：系统架构示意图 – 用户通过界面与代理交互，代理核心管理上下文并调用 LLM 和工具。*

在该架构中，**用户界面**可以是命令行工具，也可以是IDE扩展插件；**代理核心**则包含任务规划器、子代理调度、上下文处理等功能。**上下文存储**用于缓存代码文件和以往对话；**数据库/日志**用于记录操作轨迹。**模型接口**负责与OpenAI、Anthropic等API交互，或调用本地部署的模型。**工具模块**则提供执行Shell命令、文件I/O、远程搜索等能力。数据流示例：用户请求→代理引擎→代码分析/检索→模型推理→结果返回并可能提交变更→记录日志。为确保高可用性和安全性，可将这些组件容器化部署，各部分通过安全RPC/消息总线通信，并实施API密钥管理、权限隔离和审计日志等安全策略。  

## 实施步骤与里程碑  
项目实施可分为若干阶段，每阶段输出明确可验收成果，并对应相应人员及风险：  

1. **需求分析与技术选型** – **输入**：项目目标、功能需求、现有资料；**输出**：需求文档、系统架构方案、技术栈选型报告；**负责人**：产品经理、技术架构师；**工期**：约1–2周；**风险**：需求不明确导致返工，市场调研不足。  
2. **原型开发与验证** – **输入**：自行编写的代理核心、基础模型 API（Pi/DSH 仅作为竞品参考，不作为依赖）；**输出**：可运行的原型系统，能完成基础的“读代码-生成修改”循环；**负责人**：AI工程师、后端工程师；**工期**：约2–4周；**风险**：模型输出质量不佳、接口不兼容、开发环境配置复杂。  
3. **功能扩展与集成** – **输入**：原型系统、需求文档；**输出**：集成代码编辑、测试执行、子任务分配等功能；完成CLI和/或IDE插件开发；**负责人**：后端工程师、前端/插件工程师；**工期**：4–6周；**风险**：多工具集成复杂度高、并发子任务调度bug。  
4. **上下文管理与优化** – **输入**：基础系统；**输出**：实现上下文摘要、检索增强（RAG）、多代理并行执行等功能；**负责人**：AI工程师、算法工程师；**工期**：3–5周；**风险**：上下文超限导致错误，成本增加，检索效果不理想。  
5. **部署与CI/CD** – **输入**：开发完成的系统代码；**输出**：Docker镜像、部署脚本或Kubernetes配置，持续集成流水线（自动化测试、版本发布）等；**负责人**：DevOps工程师；**工期**：2–4周；**风险**：环境兼容性问题，部署安全配置不当。  
6. **测试与验证** – **输入**：部署后的系统；**输出**：测试报告（功能测试、性能测试、安全测试）、问题修复清单；**负责人**：QA工程师；**工期**：3–4周；**风险**：关键缺陷未发现，压力测试不充分。  
7. **优化与安全审计** – **输入**：测试反馈；**输出**：优化方案、补丁更新、安全审计报告；**负责人**：安全专家、开发人员；**工期**：2–3周；**风险**：隐私泄露风险、合规要求未满足。  
8. **发布与培训** – **输入**：最终系统、使用文档；**输出**：上线系统、用户培训材料；**负责人**：产品经理、技术支持；**工期**：1–2周；**风险**：用户接受度低、现场运行问题。  

每个里程碑的具体投入可根据项目规模作进一步细化。人员方面，建议配置：项目经理 1 名，系统架构师/AI 专家 1–2 名，后端/前端开发各 1–2 名，DevOps/运维 1 名，QA 测试 1 名，共计 5–7 人协作。若按每人每月约 5 万元预算估算（含工资、设备及模型费用），总周期 4–6 个月则总成本在 **100–200 万元** 量级。风险管理措施包括保持与模型提供商的沟通（避免API变更）、采用分支上下文和配额控制（防止滥用模型）、对关键数据加密和设权限（防止数据泄露）等。  

## 未指定信息与后续选项  
- **目标平台**：未指定。可考虑跨平台方案（Node.js/Go 兼容Windows/Linux/macOS），或专注 Web 环境（Electron/Vue）。需权衡部署便利性与性能要求。  
- **编程语言/框架**：未指定。可选JavaScript/TypeScript（Pi、OpenCode等生态）、或Python（丰富AI库），或Go/Rust（高性能）；各语言社区支持与团队熟悉度不同。  
- **预算与成本**：未指定。低预算下可优先采用开源组件（如OpenCode+开源模型）；若预算充足可考虑商业API（Codex/Claude）以节省开发成本。  
- **时间窗口**：未指定。短期内可先发布核心功能原型，长期则按里程碑推进完整系统。  
- **合规要求**：未指定。如需满足GDPR/HIPAA等，应采用本地化部署和数据加密等措施；若对敏感数据无特殊要求，可使用云服务提高效率。  
- **其他依赖**：若使用外部LLM服务，还需考虑网络环境、API限额和服务稳定性等因素；或可预留本地化模型的替代方案（如GPT-4All、Gemini 本地版等）以备高峰时使用。  

## 结论与建议  
综合对比分析可知，开源方案（Pi、DSH、OpenCode）提供了灵活可定制的设计参考，但成熟能力仍需要较高技术投入；商业方案（Codex、Claude Code）成熟度高、集成度强，但成本高且依赖外部服务。由于考核明确禁止在现成 agent 产品上封装界面或依赖 agent 框架/SDK，Pi、DSH、OpenCode、Codex、Claude Code 都只能作为竞品和设计参考，提交项目应自行编写 AgentLoop、工具执行、上下文和错误处理。建议以自研 CLI MVP 为核心，通过允许使用的模型厂商 API 客户端或 OpenAI 兼容接口调用模型，并逐步扩展规则、Git、MCP 等能力。无论哪种方案，都应重点考虑数据安全、权限控制与持续监控，并留出扩展多种模型和工具的接口。后续维护可以通过模块化架构简化升级（如更换模型、增加新插件），并定期评估新模型/新产品以保持竞争力。最终，本方案将以敏捷迭代方式实施，在完成各里程碑后形成可交付报告和原型系统，确保项目具有可执行性与可持续演进的能力。  

**主要信息来源：** 官方文档和产品主页（如Pi 文档、DeepSeek Harness 页面、OpenAI/Anthropic 发布文章、OpenCode GitHub等）、技术博客与新闻资讯。这些资料帮助我们详细掌握了各产品的功能特性和运行要求，为方案制定提供了依据。

---

# 补充调研：主流 AI 编程工具的功能全景与实现优先级

> 调研日期：2026-08-27。以下判断优先依据产品官方文档；“未见官方说明”不等同于产品绝对不支持。产品版本、价格和可用平台变化很快，提交前应再次核对。调研过程与验收标准见 [research-plan.md](research-plan.md)。

## 既有报告的事实校正说明

原报告中的产品名单和架构分析可以作为背景，但部分价格、模型上下文长度、版本号（例如 DSH rc.8）和“支持多少模型/提供商”等数字没有附带可复核链接，且会随版本快速变化。本补充版不再把这些数字作为结论依据；涉及 Pi、DSH、OpenCode 的具体功能，提交前应以其当前仓库 README/文档逐项复核。尤其不要在 README 或面试中承诺未经当前官方资料确认的 400 万 token 上下文、固定订阅价格或固定提供商数量。能力取舍依据的是可重复观察到的功能类别，而非这些易变数字。

## 一、先把“AI 编辑工具”分成四种产品形态

市场上常被统称为 AI 编程工具的产品，其能力边界并不相同：

1. **代码补全/编辑增强**：在光标处预测下一段代码、根据选区改写或解释代码，典型是 Gemini Code Assist、Continue Autocomplete、GitHub Copilot inline suggestions。它们不一定能自主执行命令。
2. **IDE 原生代理**：嵌入编辑器，拥有仓库搜索、文件编辑、终端和对话上下文，典型是 Cursor Agent、Windsurf/Devin Desktop Cascade、Zed Agent、JetBrains AI Assistant/Junie、Cline。
3. **终端/仓库代理**：以 CLI 为主，通过工具循环完成跨文件任务，典型是 Claude Code、Codex CLI、Aider、OpenCode、GitHub Copilot CLI。
4. **云端软件工程师**：在隔离云环境中接收 Issue/PR 或自然语言任务，异步完成修改并返回分支或 PR，典型是 GitHub Copilot cloud agent、Codex cloud、Devin。它们的环境编排、权限和成本远超本题个人 MVP。

因此，本题的“编程智能体”应对齐第 2、3 类的**本地可执行代理闭环**，而不是把单纯补全插件或云端托管服务当作实现目标。

## 二、统一功能矩阵

符号说明：✓=官方明确支持；△=部分支持、需要用户批准或依赖外部集成；—=不是该产品的主要能力；?=本轮官方资料未确认。矩阵描述的是产品形态，不代表每个订阅档位都可用。

| 产品 | 文件/终端 | 仓库上下文/搜索 | 计划/只读模式 | 测试/调试 | Git/PR | MCP/插件 | 规则/记忆/工作流 | 并行/云端 | 补全/IDE 编辑 | 主要交互面 |
|---|---|---|---|---|---|---|---|---|---|---|
| Claude Code | ✓ | ✓ 全仓库理解 | △ 计划审查 | ✓ 测试、lint、依赖、合并冲突 | ✓ commit、branch、PR | ✓ MCP | ✓ `CLAUDE.md`、skills、hooks、memory | ✓ 子代理、后台/定时任务 | △ VS Code/JetBrains 扩展 | CLI、IDE、桌面、Web |
| OpenAI Codex | ✓ 本地 shell/补丁 | ✓ 上下文管理 | ✓ Plan/审批/自动审查相关能力 | ✓ 代码任务、审查、CI 工作流 | ✓ GitHub/GitLab、PR | ✓ MCP、Skills/Plugins | ✓ `AGENTS.md`、Skills、Rules | ✓ 隔离 cloud、并行任务、worktree | △ IDE 扩展 | CLI、桌面、Web、IDE |
| GitHub Copilot cloud agent/CLI | ✓ | ✓ 仓库研究与上下文 | ✓ 研究→计划→分支 | ✓ 测试、修 bug、技术债 | ✓ 分支、PR、冲突 | ✓ MCP、custom agents | ✓ instructions、skills、hooks | ✓ 后台云 agent；CLI 可并行 | ✓ 另有 inline suggestions | GitHub、CLI、IDE、Mobile |
| Cursor Agent | ✓ 读写文件、终端 | ✓ 文件搜索、代码库搜索、Web | ✓ Plan mode、Ask/read-only | ✓ 调试、运行检查、Agent Review | ✓ diff、commit、PR 管理 | ✓ MCP、Plugins | ✓ `.cursor/rules`、`AGENTS.md`、skills | ✓ subagents、cloud agents、worktree | ✓ 原生编辑器与 inline 工作流 | 独立 IDE、Agents Window、CLI |
| Windsurf/Devin Desktop Cascade | ✓ 文件、终端、依赖安装 | ✓ Search/Analyze/Web Search | ✓ Code/Plan/Ask | ✓ 终端、linter、应用部署 | △ Git/worktree；PR 依流程 | ✓ MCP（stdio/HTTP/SSE） | ✓ Memories、Rules、AGENTS.md、Skills、Workflows | ✓ simultaneous Cascades、worktree、云能力 | ✓ IDE 内嵌 | IDE、桌面 |
| Cline | ✓ 读写文件、命令、浏览器 | ✓ 代码库探索 | ✓ Plan 与 Act 分离 | ✓ Act 中可运行测试/修复 | △ 本地 Git；Kanban 提供 worktree/auto-commit | ✓ MCP、扩展 | ✓ slash commands、规则/自定义提示 | △ Kanban 并行 | ✓ VS Code/JetBrains 内嵌 | IDE、CLI、Kanban |
| Aider | ✓ 终端编辑 | ✓ Git repo map、相关文件上下文 | △ architect/chat 模式 | ✓ lint/test 工作流 | ✓ 自动 commit、`/undo` | △ 多模型/脚本扩展 | △ conventions、in-chat commands | — | △ IDE 外部集成 | CLI、脚本、浏览器 |
| OpenCode | ✓ | ✓ LSP/仓库上下文 | ✓ Build（读写）/Plan（只读） | ✓ 代理循环中执行命令 | △ Git 工作流 | ✓ 多模型/插件生态 | △ agents/配置 | ✓ 多会话并行 | △ 桌面/IDE 集成 | CLI、桌面、IDE |
| Zed Agent | ✓ 读写、运行代码 | ✓ 项目上下文 | △ 线程/外部 agent 的模式取决于集成 | ✓ 代码生成、重构、调试 | △ Git worktree 隔离 | ✓ LLM provider、External Agent | △ 线程摘要/历史 | ✓ 多线程、worktree | ✓ 编辑器内嵌、inline edits | Zed IDE、终端线程 |
| JetBrains AI Assistant/Junie | ✓ | ✓ 项目上下文 | ✓ coding agent 多步任务 | ✓ 测试、文档、重构、问题发现 | △ PR/commit 摘要，取决于 IDE 集成 | △ 外部 agent/模型配置 | △ 团队规则与 IDE 配置 | ? | ✓ inline completion/next edit | JetBrains IDE |
| Gemini Code Assist | △ 代码/IDE 操作 | ✓ 本地 codebase awareness | ✓ Agent mode | ✓ 补全、单测、调试、文档 | ✓ GitHub code review（产品文档） | △ Google Cloud/扩展 | ✓ Enterprise 私有代码定制 | ? | ✓ 原生补全和编辑 | VS Code、JetBrains、Android Studio、GitHub |
| Continue | ✓ IDE/CLI 工作流 | ✓ codebase/context provider | △ Agent/Chat 能力依配置 | △ 由工具和模型配置决定 | △ 开源扩展 | ✓ MCP、model/context provider | ✓ rules/prompts/config | ? | ✓ Autocomplete、Edit | VS Code、JetBrains、CLI |
| Devin | ✓ Shell/IDE/Browser/Computer | ✓ 仓库索引、DeepWiki | ✓ 任务计划与会话 | ✓ 测试、部署、PR 工作流 | ✓ PR/Stacked PR | ✓ 外部集成 | ✓ Knowledge、`AGENTS.md`、环境蓝图 | ✓ 云端会话、团队编排 | △ 云 IDE | Web、桌面、IDE、Slack |

### 矩阵中最有价值的观察

- **所有成熟代理的共同最小闭环**都是：理解任务 → 获取仓库上下文 → 读文件/搜索 → 修改文件 → 执行命令验证 → 根据输出修正 → 向用户报告 diff/结果。产品名称、模型和 UI 可以替换，这个闭环不能缺。
- **Plan/Ask/Act 是权限和认知边界，不只是 UI 标签**。Cline 的 Plan mode 明确禁止改文件和执行命令；Cascade 的 Ask mode 只允许搜索分析；Cursor Agent 则把工具、模型和规则组合成可持续循环。实现时应把“只读分析”和“可执行修改”做成状态机，而不是只在提示词里提醒模型。
- **上下文管理已经从“把所有文件塞进 prompt”发展为检索和压缩**。Aider 的 repo map、Cursor 的代码库搜索、Copilot/Devin 的仓库索引、Claude Code 的全仓库理解，都说明需要文件筛选、预算和摘要策略。
- **审批和可回滚是成熟产品的共同安全层**。Cline 要求每个动作显式批准；Cursor 用 checkpoints；Aider 用 Git commit/`/undo`；Codex/Claude Code 提供 sandbox、权限或审批设置。能否安全地拒绝危险命令、展示 diff、恢复改动，比增加一个花哨工具更重要。
- **MCP、Skills、Rules、Memory、Workflows 解决的是不同问题**：MCP 连接外部工具和数据；Skills/Workflows 封装可复用过程；Rules/`AGENTS.md` 固化约束；Memory 保存跨会话知识。它们都属于扩展层，不应替代核心工具执行器。
- **代码补全与代理应分开评估**。补全降低输入成本，但不要求模型拥有终端权限；本题评分对象是能读写文件、执行命令并完成任务的 agent，补全可作为后续增强。

## 三、按产品逐项补充的官方证据

### 1. Claude Code：终端代理能力最完整的参考样本

Anthropic 官方概览明确描述：Claude Code 能读取代码库、编辑文件、运行命令，可在终端、IDE、桌面和浏览器使用；可跨多文件构建功能、修复 bug，并自动写测试、修 lint、更新依赖、解决合并冲突。它直接操作 Git，可暂存、写 commit message、创建 branch 和 pull request；MCP 可连接 Google Drive、Jira、Slack 等外部系统；`CLAUDE.md`、Skills、Hooks 和 auto memory 用于规则、复用流程和跨会话知识；还支持并行 agent、后台会话和定时任务。

对本题的启示是：先实现可解释的本地工具循环，再把规则文件、hooks 和并行 agent 作为扩展。Claude Code 的 Agent SDK 属于题目禁止依赖的现成 agent SDK，不能直接嵌入项目。

### 2. OpenAI Codex：隔离环境、Skills 和云端并行

OpenAI 开发者文档将 Codex cloud 定义为“在隔离云环境中并行运行 coding tasks”，环境可以预置依赖、工具、变量和 secrets，完成后查看摘要/diff、要求跟进或打开 PR。Codex CLI 文档还把权限、Profiles、Sandboxing、Auto-review、MCP、`AGENTS.md`、Subagents、Git worktrees、非交互模式等列为独立能力。Skills 文档说明，一个 Skill 是包含 `SKILL.md`、可选脚本和参考资料的可复用工作流，可通过 `/skills` 或 `$` 调用。

对本题的启示是：环境配置和权限边界本身就是产品能力；但云环境、Secrets 管理和并行 worktree 的实现成本高，个人版本只需要本地工作目录、命令白名单/确认、超时和 diff 回滚即可。

### 3. GitHub Copilot cloud agent/CLI：从 Issue 到 PR 的工程集成

GitHub 官方文档描述 cloud agent 可以研究仓库、创建实现计划、在 branch 上修改代码；它能修 bug、实现增量功能、提高测试覆盖率、更新文档、处理技术债和解决合并冲突。官方 code review 文档还说明，review 可以获取完整项目上下文，并把建议传给 cloud agent 自动创建带修复的 PR。文档导航同时列出了 CLI 的工具审批、并行任务、会话持久化、LSP、Hooks、Skills、MCP、沙箱和回滚等能力。

对本题的启示是：Git 集成和测试结果是“可交付的软件工程闭环”，但 GitHub 云端权限、Actions runner 和 PR 自动化不应成为 MVP 的前置依赖。

### 4. Cursor：工具编排、规则、子代理和审查

Cursor 官方 Agent 文档把 Agent 拆成 Instructions、Tools、Model 三部分；工具包括文件/目录搜索、关键词搜索、读取文件、编辑文件、终端、Web、浏览器、图像生成和提问。Agent Review 可手动、按 slash command 或每次 commit 后触发，并支持 Quick/Deep 两种深度。Rules 文档支持项目规则、用户规则、团队规则和 `AGENTS.md`；MCP 文档支持 stdio、SSE、Streamable HTTP 三种传输，并提供 Tools、Prompts、Resources、Roots、Elicitation 等协议能力；Subagents 文档强调独立上下文、并行执行和专门化配置。

对本题的启示是：工具定义应是结构化协议；规则应从系统提示中独立出来；审查应读取真实 diff 并运行检查，而不是让模型凭空评价自己的输出。

### 5. Windsurf/Devin Desktop Cascade：模式、记忆和可复用工作流

当前官方文档以 Devin Desktop/Cascade 名义维护。Cascade Overview 列出 Code/Chat、Plan/Todo、queued messages、tool calling、voice、named checkpoints/reverts、linter integration、Web Search、Memories/Rules、MCP 和 Workflows。Modes 文档规定 Code 可创建/编辑/删除文件、运行终端、安装依赖并执行多步任务；Plan 可探索代码库并生成外部 Markdown 计划；Ask 只能搜索和分析。MCP 支持 stdio、Streamable HTTP、SSE 与 OAuth。Memories/Rules 文档区分自动记忆、版本化规则、`AGENTS.md`；Workflows 是 Markdown 文件，通过 `/workflow-name` 手动调用，可组合 PR review、部署、测试和格式化步骤。

对本题的启示是：计划文件、队列和 checkpoint 都能显著改善长任务体验；slash command 适合把固定演示流程做成可复现脚本，但不应让工作流绕过用户审批。

### 6. Cline：显式人机协同和 Plan/Act 分离

Cline 官方概览称其同时存在于编辑器和终端，可读写文件、运行命令、使用浏览器，且每个动作都需要显式批准；支持 VS Code、JetBrains、CLI 和 Kanban。Plan & Act 文档规定 Plan 模式可以读代码和搜索但不能修改文件或执行命令，Act 模式沿用完整规划上下文后再执行；官方还提供 `/deep-planning` slash command。Kanban 应用可通过独立 worktree、自动 commit 和依赖链并行运行多张任务卡。

对本题的启示是：批准策略应位于工具执行器，而不是只靠模型自律；Plan→Act 的上下文继承是低成本、强可演示的功能。

### 7. Aider：轻量终端、Git 可追踪和 Repo Map

Aider 官方 Usage 文档定位为终端中的 AI pair programming：启动时指定要编辑或创建的文件，模型可自动吸收相关文件上下文；支持多种云端和本地模型。修改以 diff 展示，Aider 自动 Git commit，用户可以用 `/undo` 撤销；文档目录还包含 linting/testing、in-chat commands、chat modes、scripting、Git integration 和 repository map。Repo Map 页面说明它用仓库结构和符号关系为模型挑选上下文，避免把整个仓库直接塞进对话。

对本题的启示是：Git commit、undo、repo map 和 CLI 可组合成很小但可信的实现；不要为了展示“全自动”而牺牲可追踪性。

### 8. OpenCode：开源、多模型、Build/Plan 双代理

现有报告对 OpenCode 的描述可保留，但应补充能力分级：Build 代表可读写执行，Plan 代表只读分析；其多模型供应商、终端/桌面/IDE、多会话和 LSP 能力是开源产品的主要卖点。它适合作为“可定制代理”的参考，但具体插件和高级特性版本变化较快，引用时应链接项目当前文档而非二手文章。

### 9. Zed：原生 Agent Panel 与外部 Agent 协议

Zed 官方 Agent Panel 文档称 Agent 可以读、写、运行项目代码，用于生成、重构、调试、文档和问答；面板显示模型正在使用的工具，支持新线程、历史恢复、消息编辑和队列。多个线程可独立运行，必要时使用 Git worktree 隔离。Zed 还区分自有 Zed Agent 与通过 ACP 接入的 External Agent，部分 checkpoint、token usage 等能力取决于外部 agent。

对本题的启示是：把“代理核心”和“交互前端”解耦，未来可以用 CLI、TUI 或 IDE 作为多个前端；但题目禁止依赖现成 agent SDK，因此只能借鉴协议思想，自行实现核心循环。

### 10. JetBrains AI Assistant/Junie：IDE 原生上下文与多步 coding agent

JetBrains 官方文档称 AI Assistant 同时提供 AI Chat、编辑器内生成/更新、inline completion/next edit，以及可以跨多个文件处理多步任务的 coding agents；还覆盖解释代码、重构、发现问题、生成文档、单元测试、commit message 和 PR summary，并允许订阅、BYOK、集成 agent 或外部 agent 等多种配置。

对本题的启示是：IDE 集成的核心价值在于选区、光标、诊断和项目索引上下文，而不是必须自建完整 IDE；个人实现优先做 CLI，避免把时间消耗在编辑器 UI。

### 11. Gemini Code Assist：补全、单测、调试和企业代码定制

Google Cloud 官方文档说明 Gemini Code Assist 可在 VS Code、JetBrains 和 Android Studio 提供代码补全、从注释生成函数/代码块、生成单元测试、调试、理解和文档帮助；响应可包含来源引用。Standard/Enterprise 版本还区分私有代码仓库定制、企业安全和 Google Cloud 集成，文档导航包含 Agent mode、GitHub code review、代码库感知、文件排除、日志和审计。

对本题的启示是：来源引用、文件排除和日志是企业级增强项；最小 agent 不需要实现补全模型，但应至少能把命令输出和文件路径作为可审计上下文展示。

### 12. Continue：开源配置化的补全与代理层

Continue 官方 Autocomplete 文档提供 Codestral、开源 QwenCoder、Ollama 本地模型等配置，并把 autocomplete 作为独立 model role；官方导航同时覆盖 Agent、Chat、Edit、MCP servers、Rules、Prompts、Context Providers 和 Model Capabilities。

对本题的启示是：模型角色（补全、规划、执行、审查）可以分离配置；本题可先使用一个模型完成闭环，再预留 `ModelProvider` 接口，不要把单一模型写死在工具逻辑里。

### 13. Devin：云端完整软件工程流程的边界样本

Devin 官方文档将其定位为 AI software engineer，覆盖仓库索引、环境蓝图、Shell/IDE/Browser/Computer、PR review、Stacked PR、知识库和自托管 Outposts。它适合说明云端代理还需要环境快照、依赖和 secrets、团队治理与持久会话；这些是成熟 SaaS 的基础设施能力，不应作为个人考核 MVP 的必做项。

## 四、哪些是核心，哪些是锦上添花

### P0：本题 MVP 的核心（必须可靠）

1. **模型适配器**：API key 从环境变量读取；统一 chat/tool calling 响应；保留模型名、超时和 token/错误信息。
2. **会话与上下文状态**：保存用户消息、模型消息、工具调用和结果；限制上下文预算；必要时摘要旧消息；明确当前工作目录。
3. **本地工具协议**：至少实现 `list/read_file`、`search`、`write_file` 或统一 `apply_patch`、`run_command`；参数 JSON schema、结果截断、超时和退出码都由自己处理。
4. **可控代理循环**：模型请求→解析工具调用→执行→把结果回传→继续，设置最大轮数、重复调用检测、空响应/非法 JSON 处理和成功终止条件。
5. **安全审批**：路径限制在工作区；危险命令、网络访问和删除操作默认询问；执行前展示命令，执行后展示 stdout/stderr；API key 脱敏。
6. **验证闭环**：至少执行项目已有测试或用户指定命令；把失败输出重新交给模型进行有限次修复；最终报告修改文件、测试命令、结果和未解决问题。
7. **可审查变更**：生成 unified diff 或变更摘要；失败时不吞错误；保留运行日志。视频中应能证明“模型真的调用了本地工具并完成验证”。

这 7 项直接对应题目要求的读写文件、执行命令、对话历史与上下文、工具定义与本地执行、输出解析、循环终止和错误处理，也是面试最可能追问的部分。

### P1：强烈建议加入（提高成功率和演示质量）

- **Plan/Act 或只读/执行模式**：先探索和列计划，再明确切换到修改阶段。
- **仓库上下文选择**：忽略 `.git`、依赖和二进制；按文件名/关键词搜索；限制单文件和总上下文大小；可选生成简单 repo map。
- **Git 集成**：显示 `git diff`、允许用户确认后 commit；必要时保存临时分支或快照。
- **Slash commands/Skills/Rules**：例如 `/plan`、`/test`、`/review`；项目规则文件约束语言、测试和风格；固定流程用 Markdown 模板保存。
- **稳健恢复**：命令失败重试上限、解析失败要求模型重试、用户中断、checkpoint/备份和一键回滚。
- **可观测性**：每轮记录耗时、工具名、参数摘要、输出长度、错误类别和最终状态，便于面试解释。

### P2：锦上添花（除非 P0/P1 已稳定，不建议本题实现）

- MCP 多传输协议、插件市场和远程 OAuth。
- 多代理编排、并行任务、Git worktree 自动合并。
- 浏览器控制、截图/视觉、语音输入、图像生成。
- 云端隔离执行、后台定时任务、移动端/Slack/Issue/PR 入口。
- LSP 深度集成、语义索引、向量数据库和跨会话自动记忆。
- IDE 原生补全、复杂 TUI/桌面 UI、企业 SSO/RBAC/审计平台。

这些能力确实是市场差异化方向，但会引入新的权限面、并发一致性、部署和测试成本；做成“未来路线”比在截止日前做一个不稳定的半成品更容易辩护。

### 能力取舍速查表

| 能力 | 市场普及度/证据 | 对本题价值 | 实现复杂度 | 决策 |
|---|---|---:|---:|---|
| 文件读取、搜索、补丁/写入 | Claude Code、Cursor、Cline、Aider 等均为基础工具 | 极高 | 中 | P0，必须自研 |
| Shell/测试执行 | 几乎所有代理都有；成熟产品配合审批、超时和沙箱 | 极高 | 中-高 | P0，先做本地受控执行 |
| AgentLoop、tool calling 解析、终止/重试 | 代理产品的共同内核；题目明确要求自行实现 | 极高 | 中 | P0，面试重点 |
| 上下文筛选、截断、摘要 | repo map、代码库索引、全仓库理解已成标配 | 极高 | 中 | P0 基础筛选，P1 再做摘要/repo map |
| 错误回传和失败修复 | Claude/Cline/Cursor 等都把测试失败继续交给 agent | 极高 | 中 | P0，限制重试次数 |
| Diff、审批、回滚 | Cline approval、Cursor checkpoints、Aider undo 等反复出现 | 极高 | 中 | P0/P1，至少 diff+确认+备份 |
| Plan/Act 或 Ask/Code | Cline、Cascade、OpenCode 等采用双模式 | 高 | 低-中 | P1，强烈建议 |
| Git diff/commit/branch | Claude Code、Aider、Copilot/Codex 云端支持 | 高 | 中 | P1，至少 diff 和可选 commit |
| 规则文件/AGENTS.md | Cursor、Claude、Codex、Cascade、Devin 均支持类似机制 | 中-高 | 低 | P1，低成本高收益 |
| Slash command/Skills/Workflows | Claude、Codex、Cascade、Cline 形成可复用流程 | 中 | 低-中 | P1，选 1-2 个演示用命令 |
| MCP/插件系统 | Cursor、Claude、Codex、Cascade、Cline 等广泛支持 | 中 | 高 | P2，预留接口即可 |
| LSP/语义索引/向量 RAG | IDE 和企业产品常见 | 中 | 高 | P2，先用文本搜索 |
| 子代理/并行/worktree | Cursor、Claude、Codex cloud、Cline Kanban 等支持 | 中 | 很高 | P2，个人项目暂缓 |
| 浏览器/截图/语音/图像 | Cursor、Cascade、Devin 等差异化功能 | 低-中 | 很高 | P2，除非演示任务明确需要 |
| 云端隔离、定时任务、移动/Slack | Codex cloud、Copilot cloud、Devin 等 SaaS 能力 | 低（本题） | 很高 | P2，不作为本地 MVP 依赖 |
| IDE 原生补全 | Gemini、Continue、JetBrains、Copilot 等主打 | 低（对 agent 评分） | 高 | P2，CLI 完成后再考虑 |
| 企业 SSO/RBAC/审计 | Gemini Enterprise、Cline Enterprise、Devin 等 | 低（个人考核） | 很高 | P2，写入安全路线即可 |

“市场普及度”表示在本轮样本中出现的频率，不是市场份额排名；“实现复杂度”按个人在截止日前自行实现估计。

## 五、针对本考核的建议 MVP 架构

```text
CLI/TUI
  └─ SessionStore（消息、工具轨迹、摘要、运行状态）
      └─ AgentLoop（请求模型 → 解析 → 审批 → 执行 → 回传 → 终止判断）
          ├─ ModelProvider（OpenAI-compatible / 厂商客户端）
          ├─ ToolRegistry（read/search/patch/shell/test）
          ├─ WorkspaceGuard（路径、命令、超时、网络和密钥脱敏）
          ├─ ContextBuilder（文件筛选、repo map、截断/摘要）
          └─ Verifier（测试、lint、diff、结果摘要）
```

建议的演示任务应同时证明读、写、执行和修复，例如：给一个已有的 Python/Node 小项目添加功能；agent 先列计划并读取相关文件，修改实现和测试，运行测试发现一个失败，读取错误后修正，再展示 `git diff` 和测试通过结果。这样 2 分钟视频能覆盖题目要求，也能自然解释每个自主实现的模块。

## 六、截至截止日前的执行顺序

1. **第 1 天**：确定语言和模型 API；实现 `read/search/run` 工具、JSON schema、环境变量密钥读取和最小单轮调用。
2. **第 2 天**：实现 AgentLoop、工具调用解析、最大轮数、超时、错误回传和测试；用一个真实小任务跑通闭环。
3. **第 3 天**：实现 apply patch/文件写入、路径保护、危险命令审批、diff 展示和日志；补充非法输出与命令失败测试。
4. **第 4 天**：加入 Plan/Act、上下文裁剪、项目规则和 `/test` 或 `/review` 命令；完善 README 运行说明。
5. **第 5 天**：录制并剪辑 2 分钟视频，检查 API key 不出现在终端、日志、README 或画面中；整理公开仓库提交历史。
6. **第 6 天（截止前）**：从干净环境复现安装和运行，复核 README 不超过 1000 字，固定最终 commit，按题目要求只提交姓名命名的 zip。

## 七、来源清单（官方）

- [考核题目 PDF（本地文件）](../assignment/推免考核题目学生版.pdf)
- [Claude Code Overview](https://docs.anthropic.com/en/docs/claude-code/overview)
- [OpenAI Codex 文档](https://developers.openai.com/codex)、[Codex cloud](https://developers.openai.com/codex/cloud)、[Build skills](https://developers.openai.com/codex/skills)
- [GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)、[Copilot code review](https://docs.github.com/en/copilot/concepts/agents/code-review)
- [Cursor Agent](https://cursor.com/docs/agent/overview)、[Agent Review](https://cursor.com/docs/agent/agent-review.md)、[Rules](https://cursor.com/docs/rules.md)、[MCP](https://cursor.com/docs/mcp.md)、[Subagents](https://cursor.com/docs/subagents.md)
- [Windsurf/Devin Desktop Cascade Overview](https://docs.windsurf.com/windsurf/cascade)、[Modes](https://docs.windsurf.com/windsurf/cascade/modes)、[MCP](https://docs.windsurf.com/windsurf/cascade/mcp)、[Memories & Rules](https://docs.windsurf.com/windsurf/cascade/memories)、[Workflows](https://docs.windsurf.com/windsurf/cascade/workflows)
- [Cline Overview](https://docs.cline.bot/cline-overview.md)、[Plan & Act Mode](https://docs.cline.bot/features/plan-and-act.md)
- [Aider Usage](https://aider.chat/docs/usage.html)、[Repository map](https://aider.chat/docs/repomap.html)
- [OpenCode 文档](https://opencode.ai/docs/)
- [Zed Agent Panel](https://zed.dev/docs/ai/agent-panel)、[Zed AI overview](https://zed.dev/docs/ai/overview)
- [JetBrains AI Assistant](https://www.jetbrains.com/help/ai-assistant/)
- [Gemini Code Assist overview](https://cloud.google.com/gemini/docs/codeassist/overview)
- [Continue Autocomplete](https://docs.continue.dev/customize/deep-dives/autocomplete)
- [Devin documentation index](https://docs.devin.ai/llms.txt)
- [Replit Agent](https://docs.replit.com/replitai/agent)
- [Replit Agent Plan mode](https://docs.replit.com/features/agent/plan-mode)、[App testing](https://docs.replit.com/features/agent/app-testing)

## 八、结论（补充版）

本次补充调研将“市场功能清单”收敛成一个清晰判断：**核心竞争力不是接入多少模型或拥有多少 UI，而是能否在权限可控、上下文足够、结果可验证、失败可恢复的条件下稳定完成本地软件工程闭环。** 对本题而言，P0 的工具协议、AgentLoop、上下文、审批、安全、验证和日志必须由自己实现；P1 的 Plan/Act、repo map、规则、slash command、Git diff 和回滚最值得投入；MCP、并行子代理、浏览器、云端和 IDE 补全应作为 P2 路线。这个取舍既符合题目禁止依赖现成 agent SDK 的约束，也能在面试中用具体证据解释为什么这样设计。

---

# 九、逐产品深度功能清单（逐条证据版）

## 9.1 记录方法

下面每条使用同一格式：**功能**；行为；入口；自动程度；产出；本题优先级；官方证据。自动程度含义为：`R` 只读，`A` 执行前需批准，`X` 可自动执行，`C` 云端/异步环境。`P0` 是题目要求的最小闭环，`P1` 是强烈建议，`P2` 是后续增强。产品文档可能随版本变化；“未列出”不代表绝对不支持，只代表本次没有把它作为已证实能力。

## 9.2 Claude Code

官方证据组：[工作原理](https://code.claude.com/docs/en/how-claude-code-works.md)、[概览](https://docs.anthropic.com/en/docs/claude-code/overview.md)、[常见工作流](https://docs.anthropic.com/en/docs/claude-code/common-workflows.md)、[CLI](https://docs.anthropic.com/en/docs/claude-code/cli-reference.md)、[MCP](https://docs.anthropic.com/en/docs/claude-code/mcp.md)。

1. **仓库探索**；列出目录、读取项目说明并逐步了解代码结构；CLI 会话直接提出任务；`R/X`；形成上下文消息；`P0`；证据组同上。
2. **文件读取与编辑**；读取文件、创建文件、重命名文件并修改多个文件；终端代理和 IDE 集成；默认编辑前按权限策略批准；文件内容和 diff；`P0`；证据组同上。
3. **搜索**；按文件名、关键词和正则搜索代码，缩小需要读入的文件；代理内置搜索工具；`R/X`；匹配路径和片段；`P0`；证据组同上。
4. **Shell/构建/测试**；执行包管理、构建、测试、lint 和 Git 命令；工具调用循环；`A/X`；stdout、stderr、退出码；`P0`；证据组同上。
5. **网页检索**；搜索网页并抓取文档，作为当前任务的外部上下文；Web 工具；`A`；网页片段和引用上下文；`P1`；证据组同上。
6. **代码智能**；通过 LSP 获取类型错误、定义跳转和引用关系；代码智能工具；`R/X`；诊断和符号结果；`P1`；证据组同上。
7. **验证-修复循环**；执行测试或 lint，读取失败输出，修改代码后再次验证；agent loop 的 `gather context -> take action -> verify results`；`A/X`；多轮日志与最终测试结果；`P0`；证据组同上。
8. **Git 交付**；暂存、生成 commit message、创建分支和 pull request，处理合并冲突；CLI/GitHub 工作流；`A/X`；commit、branch、PR；`P1`；证据组同上。
9. **会话恢复**；保存 JSONL 会话，支持继续、恢复、fork、自动压缩上下文；`--continue`、`--resume` 和会话命令；`X`；持久化会话和摘要；`P1`；证据组同上。
10. **检查点与回滚**；在修改前后保存 checkpoint，可恢复文件状态；会话/编辑器界面；`A`；可比较或恢复的文件版本；`P1`；证据组同上。
11. **项目规则与记忆**；读取 `CLAUDE.md` 和 auto memory，把项目约定注入后续请求；项目文件和记忆命令；`X`；规则上下文和记忆条目；`P1`；证据组同上。
12. **Skills、Hooks、MCP**；Skill 封装可复用流程，Hook 在工具事件前后运行，MCP 连接外部工具/数据库/Issue 系统；配置文件或命令；`A/X`；外部工具结果、hook 日志；`P2`；证据组同上。
13. **子代理与并行**；把研究、实现、审查等子任务交给独立 agent，可并行并用 worktree 隔离；子代理/agent teams；`X/C`；子任务结果和分支 diff；`P2`；证据组同上。
14. **后台与定时任务**；让任务在后台或按周期运行，满足条件后返回结果；后台会话和 `/loop`；`X/C`；异步结果和通知；`P2`；证据组同上。
15. **浏览器与桌面操作**；通过 Chrome/Computer Use 导航、截图和操作界面；浏览器/桌面工具；`A/X`；截图、网络/控制台信息；`P2`；证据组同上。

## 9.3 OpenAI Codex

官方证据组：[Codex CLI](https://developers.openai.com/codex/cli.md)、[云端任务](https://developers.openai.com/codex/cloud.md)、[Skills](https://developers.openai.com/codex/skills.md)、[沙箱](https://developers.openai.com/codex/sandboxing.md)。

1. **本地仓库检查**；检查目录和 Git 状态，读取相关文件后再决定操作；CLI 交互；`R`；上下文和状态摘要；`P0`；证据组同上。
2. **文件修改**；通过补丁/编辑工具修改工作区文件；CLI 工具调用；`A`；patch 和 working-tree diff；`P0`；证据组同上。
3. **命令执行**；在本地运行 shell、构建和测试；`codex` 交互或 `codex exec`；`A/X` 取决于权限；命令输出和退出码；`P0`；证据组同上。
4. **非交互自动化**；用 `codex exec` 在脚本、CI 中执行固定提示；命令行；`X`；stdout、退出码和变更；`P1`；证据组同上。
5. **模型与推理配置**；选择模型和 reasoning effort，并通过 profile/配置设定行为；CLI 配置；无工具副作用；配置和调用元数据；`P1`；证据组同上。
6. **权限审批**；通过 `/permissions`、profiles 和 sandbox 设置读写、命令和网络权限；CLI 设置；`A`；批准/拒绝记录；`P0`；证据组同上。
7. **沙箱边界**；限制文件、网络和命令继承的访问范围；sandbox 配置；`X`；隔离执行结果或拒绝原因；`P0`；证据组同上。
8. **Git checkpoint 与恢复**；在本地保存检查点并恢复会话；CLI 会话；`A`；checkpoint、diff；`P1`；证据组同上。
9. **Plan、审查和图片输入**；支持计划/审批工作流、code review 和图片作为任务上下文；CLI 命令/输入；`R/A`；计划、审查意见、视觉上下文；`P1/P2`；证据组同上。
10. **Skills/Plugins/MCP**；用 `SKILL.md`、可选脚本和参考资料封装工作流，并连接 MCP 服务；技能和配置；`A/X`；技能输出和外部工具结果；`P2`；证据组同上。
11. **Subagents/worktrees**；启动独立子代理和 Git worktree，减少并发冲突；CLI；`X`；子任务摘要、分支 diff；`P2`；证据组同上。
12. **云端并行任务**；在隔离云环境配置依赖、环境变量、secrets 后并行执行任务；Codex cloud；`C/X`；日志、摘要、diff 和可选 PR；`P2`；证据组同上。

## 9.4 GitHub Copilot cloud agent/CLI

官方证据组：[Cloud agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)、[Code review](https://docs.github.com/en/copilot/concepts/agents/code-review)。

1. **Issue/自然语言入口**；从 Issue、PR、GitHub、Slack、Teams 或 IDE 提交任务；对应集成入口；`R/C`；任务会话；`P2`（本题可只保留 CLI）。
2. **仓库研究**；分析仓库结构、现有实现和相关文件；cloud agent 研究阶段；`R/C`；研究摘要和上下文；`P0`；证据组同上。
3. **实现计划**；先生成步骤、风险和验证计划；任务会话；`R`；可审阅计划；`P1`；证据组同上。
4. **分支修改**；在独立 branch 中编辑多文件；云端 coding agent；`X/C`；commit 和 branch diff；`P1`；证据组同上。
5. **测试与 linter**；在 GitHub Actions 临时环境运行测试和 lint，根据失败继续修改；云端 runner；`X/C`；check run 日志和结果；`P0/P2`；证据组同上。
6. **工程任务模板**；修 bug、增量功能、提升测试覆盖率、更新文档和处理技术债；任务提示；`X/C`；代码、测试和文档 diff；`P1`；证据组同上。
7. **冲突处理**；识别并处理 merge conflict；branch/PR 工作流；`A/X/C`；冲突解决提交；`P2`；证据组同上。
8. **PR 交付**；生成 commit message、push branch 并创建 PR；GitHub 自动化；`A/C`；PR、描述和检查链接；`P2`；证据组同上。
9. **Code review**；读取完整项目上下文，发现问题并给出建议，可把修复交给 cloud agent；PR review；`R/C` 后可 `A/C` 修复；评论和修复 PR；`P2`；证据组同上。
10. **自定义 agent/instructions**；配置自定义 agent、仓库 instructions、skills 和 memory；仓库/组织配置；`X`；规则和技能上下文；`P1/P2`；证据组同上。
11. **工具扩展与治理**；通过 MCP、Hooks、沙箱、工具审批和会话持久化控制工具行为；CLI/组织设置；`A/X`；审计、拒绝和工具日志；`P1/P2`；证据组同上。

## 9.5 Cursor Agent

官方证据组：[Agent](https://cursor.com/docs/agent/overview.md)、[Plan Mode](https://cursor.com/docs/agent/plan-mode.md)、[Debug Mode](https://cursor.com/docs/agent/debug-mode.md)、[Terminal](https://cursor.com/docs/agent/tools/terminal.md)、[Search](https://cursor.com/docs/agent/tools/search.md)、[Browser](https://cursor.com/docs/agent/tools/browser.md)、[Review](https://cursor.com/docs/agent/agent-review.md)、[Rules](https://cursor.com/docs/rules.md)、[MCP](https://cursor.com/docs/mcp.md)、[Subagents](https://cursor.com/docs/subagents.md)。

1. **Agent 三元组**；将 Instructions、Tools、Model 组合成一次代理运行；Agent 面板；无直接副作用；运行配置；`P0`；证据组同上。
2. **文件/目录搜索**；按目录、关键词和正则查找相关文件，支持 Instant Grep；Agent 搜索工具；`R`；路径、匹配片段；`P0`；证据组同上。
3. **文件读取**；将选定文件内容作为上下文；Agent；`R`；上下文项；`P0`；证据组同上。
4. **编辑与补丁**；对单个或多个文件生成并应用编辑；Agent 编辑工具；`A`；inline diff/checkpoint；`P0`；证据组同上。
5. **终端**；生成并执行 shell 命令，读取输出；终端工具；`A/X`；命令、stdout/stderr、退出码；`P0`；证据组同上。
6. **Web 与浏览器**；网页搜索，导航、点击、输入、滚动、截图，查看 console 和 network；Browser 工具；`A`；页面状态、截图和网络信息；`P2`；证据组同上。
7. **Plan Mode**；澄清需求、研究代码库、生成计划，经用户审阅后执行；模式选择器；`R`；计划文本；`P1`；证据组同上。
8. **Debug Mode**；提出假设、增加 instrumentation、复现、分析日志、定向修复并清理 instrumentation；Debug 模式；`A`；诊断、修复 diff；`P1`；证据组同上。
9. **Checkpoint/队列**；保存检查点，排队后续消息并支持恢复；Agent 面板；`A`；checkpoint 和消息队列；`P1`；证据组同上。
10. **Agent Review**；按 commit、slash command 或手动触发 Quick/Deep 审查；Source Control/Agent；`R`；审查结果和建议；`P1/P2`；证据组同上。
11. **Rules 与忽略文件**；读取项目、用户、团队规则及 `AGENTS.md`，用 `.cursorignore` 排除敏感/无关文件；配置文件；`X`；规则和筛选后的上下文；`P1`；证据组同上。
12. **MCP/Subagents/Hooks**；接入 MCP 的 tools/prompts/resources，启动独立上下文子代理，并在工具/会话事件前后运行 hooks；配置/Agent；`A/X`；外部结果、子代理摘要、hook 日志；`P2`；证据组同上。
13. **云端 agent**；在隔离 VM 使用桌面、浏览器、MCP 和预装依赖完成任务并返回 artifact/PR；Cloud Agent；`C/X`；日志、截图、视频、diff、PR；`P2`；证据组同上。

## 9.6 Windsurf/Devin Desktop Cascade

官方证据组：[Cascade 概览](https://docs.windsurf.com/windsurf/cascade)、[模式](https://docs.windsurf.com/windsurf/cascade/modes)、[MCP](https://docs.windsurf.com/windsurf/cascade/mcp)、[Memories/Rules](https://docs.windsurf.com/windsurf/cascade/memories)、[Workflows](https://docs.windsurf.com/windsurf/cascade/workflows)。

1. **Code 模式**；创建、编辑、删除文件，运行终端，安装依赖，完成多步自主任务；Cascade Code；`A/X`；文件 diff、命令输出；`P0`；证据组同上。
2. **Plan 模式**；探索仓库、提问、给出多个方案并写外部 Markdown 计划；Cascade Plan；`R`；计划文件；`P1`；证据组同上。
3. **Ask 模式**；只搜索和分析，不修改文件；Cascade Ask；`R`；解释和分析结果；`P1`；证据组同上。
4. **队列与自动继续**；prompt 可排队，达到工具调用上限后自动继续；Cascade 输入/运行控制；`X`；连续运行轨迹；`P1`；证据组同上。
5. **搜索/分析/Web Search**；搜索工作区、分析代码并抓取网页文档；Cascade 工具；`R`；匹配片段和网页上下文；`P0/P1`；证据组同上。
6. **依赖和 lint**；检测依赖并安装，集成 linter；Code 模式/终端；`A/X`；安装日志、lint 输出；`P1`；证据组同上。
7. **检查点与恢复**；创建 named checkpoint，回退到之前状态；Cascade 控制；`A`；checkpoint 和恢复 diff；`P1`；证据组同上。
8. **规则与记忆**；自动生成 workspace memory，读取 global/workspace/system rules 和 `AGENTS.md`；配置/项目文件；`X`；持久化上下文；`P1`；证据组同上。
9. **Skills/Workflows**；动态调用 Skill，手动用 `/name` 调用 Markdown workflow，按步骤执行测试、部署或 PR review；Customizations；`A/X`；步骤结果和日志；`P1/P2`；证据组同上。
10. **MCP**；支持 stdio、Streamable HTTP、SSE、OAuth，按工具开关控制，最多 100 个工具；MCP 面板/config；`A`；外部工具结果；`P2`；证据组同上。
11. **并行 Cascade/worktree**；运行多个 Cascade，并用 worktree 隔离工作区；Agent/Worktree；`X`；多分支 diff；`P2`；证据组同上。
12. **App Deploys**；构建并部署应用，返回可访问 URL；Deploy；`C/A`；构建日志、部署 URL；`P2`；证据组同上。

## 9.7 Cline

官方证据组：[概览](https://docs.cline.bot/cline-overview.md)、[Plan/Act](https://docs.cline.bot/core-workflows/plan-and-act.md)、[工具参考](https://docs.cline.bot/tools-reference/all-cline-tools.md)、[文件上下文](https://docs.cline.bot/core-workflows/working-with-files.md)、[检查点](https://docs.cline.bot/core-workflows/checkpoints.md)、[规则](https://docs.cline.bot/customization/cline-rules.md)、[MCP](https://docs.cline.bot/mcp/mcp-overview.md)、[Auto Approve](https://docs.cline.bot/features/auto-approve.md)、[Subagents](https://docs.cline.bot/features/subagents.md)。

1. **多端入口**；VS Code、JetBrains、CLI/TUI、Kanban 和 ACP 均可启动任务；对应扩展/命令；不统一；会话记录；`P1`；证据组同上。
2. **读取文件/目录**；单文件或 `read_files` 批量读取，并可用 `@file`、`@folder`、拖放文件作为上下文；IDE/CLI；`R`；文件上下文；`P0`；证据组同上。
3. **搜索**；使用 ripgrep 搜索代码和文件；搜索工具；`R`；匹配路径/片段；`P0`；证据组同上。
4. **应用补丁/写文件**；用 `apply_patch` 或写入工具修改、新建文件；Act 模式；`A`；patch、文件 diff；`P0`；证据组同上。
5. **Bash/命令**；执行 shell、安装依赖、构建和测试；终端工具；默认逐次批准；stdout/stderr/退出码；`P0`；证据组同上。
6. **网页抓取/浏览器**；抓取 URL，或用浏览器执行网页操作；工具面板；`A`；网页内容、截图和状态；`P1/P2`；证据组同上。
7. **Plan 模式**；只读搜索和阅读，禁止编辑和命令；模式切换；`R`；方案和风险；`P1`；证据组同上。
8. **Act 模式上下文继承**；从 Plan 继承已收集上下文后执行修改和命令；模式切换；`A`；实现 diff 和执行记录；`P1`；证据组同上。
9. **显式批准/Auto Approve**；按读项目、编辑项目、安全命令、所有命令、浏览器、MCP 等粒度设置批准；设置面板；`A/X`；批准/拒绝轨迹；`P0`；证据组同上。
10. **Checkpoint**；每次工具使用前建立 shadow Git snapshot，可比较、恢复文件或任务；自动检查点；`X/A`；版本和回滚结果；`P1`；证据组同上。
11. **规则与命令**；读取 `.clinerules`、`AGENTS.md` 等规则，提供 `/newtask`、`/compact`、`/newrule`、`/deep-planning` 等命令；CLI/输入框；`X`；规则上下文和命令结果；`P1`；证据组同上。
12. **MCP/扩展/Skills/Hooks**；连接外部工具，安装插件和技能，并在事件上执行 hook；配置；`A/X`；外部结果和 hook 日志；`P2`；证据组同上。
13. **Kanban 并行**；每张任务卡使用 worktree、auto-commit 和依赖链并行执行；Kanban；`X`；多卡状态、分支和 commit；`P2`；证据组同上。
14. **只读子代理**；启动独立 context 做研究，限制其不能编辑、浏览器、MCP 或再次创建子代理；Subagents；`R/X`；研究摘要；`P2`；证据组同上。

## 9.8 Aider

官方证据组：[Usage](https://aider.chat/docs/usage.html)、[Repo map](https://aider.chat/docs/repomap.html)、[Scripting](https://aider.chat/docs/scripting.html)、[Git](https://aider.chat/docs/git.html)。

1. **终端 pair programming**；在 CLI 对话中提出任务并迭代；`aider`；`A`；对话和 diff；`P0`；证据组同上。
2. **文件范围选择**；启动时指定文件或用 `/add` 添加文件，也可创建新文件；CLI 命令；`R/A`；活动文件集合；`P0`；证据组同上。
3. **相关上下文吸收**；根据任务自动吸收相关文件；对话循环；`X`；发送给模型的文件上下文；`P0`；证据组同上。
4. **Repo map**；提取文件、类、函数、类型、签名和关键定义，用图排序选择最相关片段；启动参数和自动过程；`X`；受 token 预算限制的结构摘要；`P1`；证据组同上。
5. **模型切换**；支持云端和本地模型并在会话中切换；CLI 选项/命令；无文件副作用；模型配置；`P1`；证据组同上。
6. **Diff 展示**；每次修改展示统一 diff，用户可在提交前检查；CLI；`R`；unified diff；`P0`；证据组同上。
7. **Git 自动提交**；默认可生成 commit message 并提交改动；Git 集成；`A/X`；Git commit；`P1`；证据组同上。
8. **撤销与历史**；`/undo` 撤销最近 AI 修改，`/diff`、`/commit`、`/git` 管理状态；CLI 命令；`A`；恢复后的工作区和日志；`P1`；证据组同上。
9. **Lint/Test**；文档导航包含 linting/testing 工作流，命令失败可回传对话；CLI/项目脚本；`A`；测试和 lint 输出；`P0`；证据组同上。
10. **脚本化**；用 `--message`、`--message-file`、`--yes`、`--dry-run`、`--commit` 执行自动化流程；CLI；`X`；stdout、diff、退出码；`P1`；证据组同上。
11. **对话模式**；提供 chat/architect 等模式，改变模型是只讨论还是负责编辑；CLI 模式；`R/A`；计划或代码变更；`P1`；证据组同上。
12. **多模型/IDE 辅助**；模型和终端为核心，IDE、语音、网页等在文档生态中作为外围入口；CLI/插件；不统一；会话结果；`P2`；证据组同上。

## 9.9 OpenCode

官方证据组：[Agents](https://opencode.ai/docs/agents/)、[Tools](https://opencode.ai/docs/tools/)、[Permissions](https://opencode.ai/docs/permissions/)、[Rules](https://opencode.ai/docs/rules/)、[Commands](https://opencode.ai/docs/commands/)、[Skills](https://opencode.ai/docs/skills/)。

1. **Build agent**；拥有文件操作和系统命令的完整工具集，可直接实现任务；Agent 选择器；`A/X`；文件 diff、命令结果；`P0`；证据组同上。
2. **Plan agent**；默认编辑和 bash 为 ask，适合只读研究与计划；Agent 选择器；`R/A`；分析和计划；`P1`；证据组同上。
3. **主代理/子代理**；主代理可调用 General、Explore、Scout 等专门子代理；Agent 配置；`X`；独立摘要；`P2`；证据组同上。
4. **文件工具**；`read`、`write`、`edit`、`apply_patch` 完成读写和精确修改；工具调用；`A`；文件内容和 patch；`P0`；证据组同上。
5. **搜索工具**；`grep`、`glob` 查找文本和路径；工具调用；`R`；匹配结果；`P0`；证据组同上。
6. **Shell 与网络**；`bash` 执行命令，`webfetch`/`websearch` 获取外部资料；工具调用；`A`；命令输出和网页内容；`P0/P1`；证据组同上。
7. **LSP**；支持 definition、references、hover、document/workspace symbols、implementation、call hierarchy；`lsp` 工具；`R`；符号和诊断；`P1`；证据组同上。
8. **工具权限**；每个工具/MCP glob 可设 allow、ask、deny，`--auto` 自动批准非 deny 请求；权限配置；`A/X`；决策和拒绝原因；`P0`；证据组同上。
9. **上下文与压缩**；隐藏 agent 负责 compaction、标题和摘要，长会话可继续；会话系统；`X`；摘要和继续状态；`P1`；证据组同上。
10. **自定义 agent**；定义 description、temperature、model、prompt、steps、permission；配置文件；无直接副作用；agent 配置；`P1`；证据组同上。
11. **命令系统**；`.opencode/commands/*.md` 支持 `$ARGUMENTS`、`!command` Shell 输出和 `@file` 文件注入，内置 `/init`、`/undo`、`/redo`、`/share`；命令入口；`A/X`；模板结果和命令输出；`P1`；证据组同上。
12. **Skills/MCP**；按需加载 `SKILL.md`，连接本地 stdio、远程 HTTP、OAuth MCP；配置；`A/X`；技能和外部工具结果；`P2`；证据组同上。
13. **多会话与分享**；并行会话和 Web 分享会话上下文；CLI/桌面；`X`；会话链接和历史；`P2`；证据组同上。

## 9.10 Zed Agent

官方证据组：[Agent Panel](https://zed.dev/docs/ai/agent-panel)、[AI 概览](https://zed.dev/docs/ai/overview)。

1. **读写运行代码**；Agent Panel 可读取、写入并运行项目代码；面板；`A`；代码 diff 和命令结果；`P0`；证据组同上。
2. **生成/重构/调试**；用自然语言完成生成、重构、调试、文档和问答；Agent thread；`A`；建议或文件变更；`P1`；证据组同上。
3. **工具可见性**；面板展示当前正在调用的工具；Agent Panel；`R`；可观察工具轨迹；`P0`；证据组同上。
4. **线程生命周期**；新建 thread、查看历史、从 summary 新建 thread；面板；`R/A`；会话历史和摘要；`P1`；证据组同上。
5. **消息控制**；编辑消息、排队消息、steering、interrupt；线程输入；`A`；更新后的请求和中断状态；`P1`；证据组同上。
6. **并行线程**；多个 thread 独立运行；Agent Panel；`X`；多线程状态；`P2`；证据组同上。
7. **Worktree 隔离**；用 Git worktree 隔离并行修改；线程/版本控制；`X`；独立目录和 diff；`P2`；证据组同上。
8. **Zed/External Agent**；区分内置 Zed Agent 与 ACP 外部 agent；Agent 设置；依集成而定；thread 和工具结果；`P2`；证据组同上。
9. **模型提供商**；支持云模型、本地模型、API key、订阅或网关；AI 设置；无直接副作用；模型配置；`P1`；证据组同上。
10. **IDE 原生编辑**；在编辑器中接受模型生成的修改并继续工作；编辑器/Agent Panel；`A`；inline 变更；`P2`；证据组同上。

## 9.11 JetBrains AI Assistant/Junie

官方证据组：[About AI Assistant](https://www.jetbrains.com/help/ai-assistant/about-ai-assistant.html)。

1. **上下文感知 Chat**；基于当前文件、选区、项目结构和最近改动回答问题；AI Chat；`R`；解释和建议；`P1`；证据组同上。
2. **跨文件 coding agent**；委托复杂多步任务，跨多个文件处理较大变更；coding agent 入口；`A/X`；多文件 diff；`P1`；证据组同上。
3. **自然语言生成/更新**；在编辑器中生成或更新代码；编辑器动作；`A`；代码片段或 inline diff；`P1`；证据组同上。
4. **Inline completion**；光标处实时补全；编辑器；`X`（用户接受）；补全文本；`P2`；证据组同上。
5. **Next edit suggestions**；预测下一处相关编辑；编辑器；`X`（用户接受）；下一编辑建议；`P2`；证据组同上。
6. **代码解释**；解释选中代码、函数和项目逻辑；Chat/编辑器动作；`R`；自然语言解释；`P1`；证据组同上。
7. **重构与问题发现**；建议重构并识别潜在问题；编辑器 AI actions；`R/A`；诊断和修改建议；`P1`；证据组同上。
8. **文档与单元测试生成**；生成注释、API 文档和测试；编辑器/Chat；`A`；文档和测试文件；`P1`；证据组同上。
9. **提交与 PR 摘要**；生成 commit message 和 pull request summary；Git 工具窗口；`R`；文本摘要；`P1`；证据组同上。
10. **模型配置**；可使用 JetBrains subscription、BYOK、provider account 或外部 agent；AI 设置；无直接副作用；模型/账户配置；`P1`；证据组同上。
11. **项目上下文选择**；把当前打开文件、选区和最近改动发送给模型，而不是默认全仓库；IDE 上下文机制；`R`；上下文包；`P0`（设计启示）；证据组同上。

## 9.12 Gemini Code Assist

官方证据组：[Overview](https://cloud.google.com/gemini/docs/codeassist/overview)、[Agent mode](https://cloud.google.com/gemini/docs/codeassist/agent-mode)、[Code customization](https://cloud.google.com/gemini/docs/codeassist/code-customization-overview)。

1. **IDE 代码补全**；在 VS Code、JetBrains、Android Studio 中提供行内代码建议；编辑器；`X`（用户接受）；补全文本；`P2`；证据组同上。
2. **注释生成函数/代码块**；从自然语言注释生成实现；编辑器/Chat；`A`；代码片段；`P1`；证据组同上。
3. **代码理解**；解释代码、回答项目问题；Chat；`R`；解释文本；`P1`；证据组同上。
4. **单元测试生成**；根据代码生成测试；编辑器动作；`A`；测试文件或 patch；`P1`；证据组同上。
5. **调试帮助**；分析错误并提出修复方向；Chat/Agent mode；`R/A`；诊断和建议 diff；`P1`；证据组同上。
6. **文档帮助**；生成或改写代码文档；编辑器/Chat；`A`；文档文本或文件；`P1`；证据组同上。
7. **Agent mode**；让 agent 依据任务使用工具完成多步代码工作；Agent mode；`A`；工具轨迹和变更；`P1`；证据组同上。
8. **代码库感知**；检索本地 codebase 相关内容，支持文件排除；IDE/配置；`R/X`；检索上下文；`P1`；证据组同上。
9. **来源引用**；响应可包含来源引用，帮助核查答案；Chat；`R`；引用链接/片段；`P2`；证据组同上。
10. **GitHub code review**；在 GitHub 工作流中审查代码；GitHub 集成；`R`；review 评论；`P2`；证据组同上。
11. **企业代码定制**；索引私有代码仓库，按计划重建索引并用于建议；Enterprise 配置；`C/X`；私有检索上下文；`P2`；证据组同上。
12. **IAM/日志/审计**；企业控制访问并记录使用；Google Cloud 管理；`X`；审计日志；`P2`；证据组同上。

## 9.13 Continue

官方证据组：[Agent Quick Start](https://docs.continue.dev/ide-extensions/agent/quick-start)、[Chat Quick Start](https://docs.continue.dev/ide-extensions/chat/quick-start)、[Edit Quick Start](https://docs.continue.dev/ide-extensions/edit/quick-start)、[Autocomplete](https://docs.continue.dev/ide-extensions/autocomplete/quick-start)、[Plan Mode](https://docs.continue.dev/guides/plan-mode-guide)、[MCP](https://docs.continue.dev/customize/mcp-tools)、[Rules](https://docs.continue.dev/customize/rules)、[Models](https://docs.continue.dev/customize/models)、[CLI](https://docs.continue.dev/cli/quickstart)。

1. **Agent 模式**；自动决定使用文件探索、搜索、编辑和命令工具来实现任务；IDE Agent 或 `cn` CLI；默认工具调用需批准；文件 diff 和命令结果；`P0`；证据组同上。
2. **Chat 模式**；无工具的对话，解释代码、回答问题并迭代方案；Chat 面板；`R`；文本/代码建议；`P1`；证据组同上。
3. **Plan 模式**；只读浏览文件、搜索、分析仓库、查看 Git 历史/diff、抓取网页和调用只读 MCP，禁止编辑、命令、安装依赖和提交；模式选择器或 `--readonly`；`R`；计划和风险清单；`P1`；证据组同上。
4. **代码上下文选择**；用选区、活动文件、`@Files`、`@Terminal`、`@Git Diff` 提供精确上下文；Chat/Agent 输入；`R`；上下文项；`P0`；证据组同上。
5. **代码库工具探索**；Agent 可读文件、搜索模式、理解项目结构并访问 Git 历史；内置工具；`R`；路径、片段、提交信息；`P0`；证据组同上。
6. **编辑模式**；选中代码后用自然语言修改，流式显示 inline diff，可逐项或全部接受/拒绝；`Cmd/Ctrl+I`；`A`（用户接受）；局部 diff；`P1`；证据组同上。
7. **Autocomplete**；独立 autocomplete model role 提供 inline 建议，Tab 接受、Esc 拒绝、快捷键部分接受；编辑器；`X`（用户接受）；补全文本；`P2`；证据组同上。
8. **CLI 双模式**；`cn` 启动 TUI，`cn -p` headless 单次执行；CLI；`A/X`；终端输出和退出码；`P0/P1`；证据组同上。
9. **CLI 权限**；支持 `--auto`、`--readonly`、`--allow`、`--exclude` 控制工具；CLI flags；`A/X`；批准/拒绝行为；`P0`；证据组同上。
10. **多模型角色**；Chat、Edit、Apply、Autocomplete、Embedding、Reranker 可分别配置不同模型/供应商，支持云和 Ollama；配置 YAML；无直接副作用；模型配置和检索结果；`P1`；证据组同上。
11. **MCP**；通过 MCP server 连接外部工具、系统、数据库；MCP 配置；`A`；外部工具结果；`P2`；证据组同上。
12. **Rules/Prompts**；`.continue/rules` 指定编码规范、安全实践和项目约定，Prompts 封装任务模板/审查流程；项目配置；`X`；规则和模板上下文；`P1`；证据组同上。
13. **工具错误回传**；工具返回的数据和多数错误自动作为 context item 回传给 Agent，模型据此决定下一步；Agent loop；`X`；错误上下文和修复结果；`P0`；证据组同上。
14. **会话恢复**；CLI 支持 `--resume` 恢复最近会话，headless 适合 CI、git hooks 和脚本；CLI；`X`；会话历史/脚本输出；`P1`；证据组同上。

## 9.14 Devin（云端软件工程师与 Devin Desktop）

官方证据组：[文档索引](https://docs.devin.ai/llms.txt)、[Session Tools](https://docs.devin.ai/work-with-devin/devin-session-tools.md)、[Devin Review](https://docs.devin.ai/work-with-devin/devin-review.md)、[Computer Use](https://docs.devin.ai/work-with-devin/computer-use.md)、[DeepWiki](https://docs.devin.ai/work-with-devin/deepwiki.md)、[环境蓝图](https://docs.devin.ai/onboard-devin/environment/blueprints.md)、[Knowledge](https://docs.devin.ai/onboard-devin/knowledge-onboarding.md)、[AGENTS.md](https://docs.devin.ai/onboard-devin/agents-md.md)、[CLI Subagents](https://docs.devin.ai/cli/subagents.md)、[CLI Sandbox](https://docs.devin.ai/cli/sandbox.md)、[Rules](https://docs.devin.ai/cli/extensibility/rules.md)、[Skills](https://docs.devin.ai/cli/extensibility/skills/overview.md)。

1. **仓库索引/DeepWiki**；自动索引仓库，生成架构图、摘要和来源链接；Web Wiki/Ask Devin；`R/C`；可检索的知识页；`P2`；证据组同上。
2. **Shell**；获得开发环境命令行访问，查看命令历史和输出，也可人工接管运行命令；Session Tools；`A/X/C`；完整命令/输出历史；`P0`（能力启示）；证据组同上。
3. **IDE**；在带仓库的 VS Code 中实时查看/修改代码，跳转定义、测试和接管任务；云端 IDE；`A/C`；编辑 diff 和测试结果；`P2`；证据组同上。
4. **Browser/Desktop**；浏览网页、处理认证、运行本地应用、截图和录制；Desktop/Computer Use；`A/X/C`；页面状态、截图、视频；`P2`；证据组同上。
5. **统一进度日志**；把 shell 命令、代码编辑和浏览器活动放在同一 Progress view；Web 会话；`R`；可追溯工作日志；`P1`；证据组同上。
6. **Side Chat**；在不中断主任务的情况下提问，能搜索/读取代码但不能编辑、执行命令或改变主会话；`/btw` 或侧栏；`R`；只读回答；`P1`；证据组同上。
7. **环境蓝图**；定义仓库、工具、依赖、环境变量、secrets，并生成可审阅的环境快照；Environment/Blueprint；`A/C`；环境构建日志和快照；`P2`；证据组同上。
8. **PR Review**；按逻辑组织 diff，检测复制移动、Bug、CWE 安全问题，支持代码库问答；Review 页面；`R/C`；分组 diff、评论和安全结果；`P2`；证据组同上。
9. **PR 操作**；评论、批准、请求修改、合并、关闭、转 draft、启用 auto-merge；Review/GitHub App；`A/C`；PR 状态和评论；`P2`；证据组同上。
10. **代码修改回写 PR**；从 review chat 请求修改，审阅后以 commit 应用到 PR branch；Review chat；`A/C`；修复 commit；`P2`；证据组同上。
11. **AGENTS.md/Knowledge**；根目录和子目录规则按路径作用域加载，Knowledge 作为跨会话组织上下文；项目/组织配置；`X`；规则和知识上下文；`P1`；证据组同上。
12. **Skills/Playbooks/Slash commands**；Skill 打包 prompt、工具、权限和 workflow，可由用户 `/skill` 或 agent 调用；CLI/项目文件；`A/X`；复用流程输出；`P1/P2`；证据组同上。
13. **安全 Profile/Secrets**；限制 network、MCP、git、GitHub CLI，并安全注入凭据；组织设置；`A/X`；策略决策和脱敏日志；`P0`（本题实现本地简化版）；证据组同上。
14. **云端并行与动态工作流**；并行托管会话，脚本 fan-out、传递结构化结果并从中断处恢复；Cloud/CLI；`X/C`；多会话结果和恢复状态；`P2`；证据组同上。
15. **部署与自动化**；通过 Slack、GitHub、Linear、Jira、webhook、schedule 启动会话，并部署独立应用；Automations/App Deploys；`X/C`；PR、部署 URL、任务日志；`P2`；证据组同上。

## 9.15 Pi（Earendil）

官方证据组：[coding-agent README](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)、[Pi monorepo](https://github.com/badlogic/pi-mono)。

1. **最小终端 harness**；以本地终端为主，强调可扩展而不是预置复杂工作流；`pi` CLI；无直接副作用；会话和工具结果；`P0`（竞品参考）；证据组同上。
2. **默认四工具**；模型默认可调用 `read`、`write`、`edit`、`bash`；交互会话；`A/X` 取决于配置；文件变更和命令输出；`P0`；证据组同上。
3. **文件引用**；输入中用 `@` 模糊搜索并引用项目文件，也支持路径补全；终端编辑器；`R`；文件上下文；`P0`；证据组同上。
4. **Bash 注入**；`!command` 执行命令并把输出发送给模型，`!!command` 只执行不发送；交互编辑器；`A`；命令输出或本地副作用；`P0`；证据组同上。
5. **多模型/供应商**；通过 `/login`、`/model` 或配置切换云端、本地和自定义 provider；CLI；无工具副作用；模型配置；`P1`；证据组同上。
6. **四种运行模式**；interactive、print、JSON、RPC，另提供 Node SDK 进行程序化集成；CLI 参数/SDK；`X`；终端输出、JSONL 或 RPC 事件；`P1`；证据组同上。
7. **会话树**；会话以 JSONL 树保存，支持 `/resume`、`/tree`、`/fork`、`/clone`、导入导出；命令；`R/A`；可恢复和分支会话；`P1`；证据组同上。
8. **上下文压缩**；手动或自动 compact 老消息，保留近期上下文，完整历史仍在 JSONL；`/compact`/设置；`X`；摘要和原始会话；`P1`；证据组同上。
9. **队列与中断**；工作时可提交 steering/follow-up 消息，Escape 中断并恢复队列；终端快捷键；`A`；队列和中断状态；`P1`；证据组同上。
10. **规则/上下文文件**；自动发现 `AGENTS.md`、`CLAUDE.md` 和覆盖文件，注入系统提示；项目启动；`X`；规则上下文；`P1`；证据组同上。
11. **Prompt/Skills/Extensions**；Markdown prompt、Skill 和 TypeScript 扩展可注册工具、命令、UI、权限、子代理、checkpoint、MCP 等；项目/用户目录；`A/X`；扩展结果和事件日志；`P1/P2`；证据组同上。
12. **工具 allowlist**；`--tools`、`--exclude-tools`、`--no-tools` 控制内置、扩展和自定义工具；CLI；`A/X`；允许/拒绝结果；`P0`（安全启示）；证据组同上。
13. **项目信任与离线**；对项目本地资源先询问 trust，可关闭启动网络操作和 telemetry；CLI/设置；`A`；信任决策和离线状态；`P1`；证据组同上。
14. **明确的功能边界**；核心不内置 plan mode、subagents、MCP、权限弹窗、后台 bash 和 TODO，建议用扩展或外部工具构建；产品设计声明；`R`；扩展方案；`P1`（说明取舍）；证据组同上。

## 9.16 DeepSeek Harness（DSH）

官方证据组：[README](https://github.com/deepseek-ai/deepseek-harness/blob/master/README.md)、[用户指南](https://github.com/deepseek-ai/deepseek-harness/tree/master/docs/user/guide)、[架构/子系统文档](https://github.com/deepseek-ai/deepseek-harness/tree/master/docs/subsystems)。

1. **一切皆插件**；模型、工具、UI、会话和运行模式都通过 Cordis 插件组合；源码/构建文档；无固定副作用；插件配置和运行轨迹；`P1`（架构参考）；证据组同上。
2. **本地 Web UI**；`npx @deepseek-ai/dsh web` 启动本地服务并在浏览器打开，默认绑定本机；CLI；`X`；Web 会话界面；`P2`；证据组同上。
3. **标准模式**；提供完整开发工具集，包括文件编辑、Shell、网络、计划、子代理和工作流；模式配置；`A/X`；工具轨迹和文件 diff；`P1`；证据组同上。
4. **Code 模式**；允许模型生成代码并逐步执行复杂操作；模式选择器；`A/X`；代码变更和命令输出；`P1`；证据组同上。
5. **Minimal 模式**；只保留基础 Shell 和编辑能力，用于基线或故障排查；模式选择器；`A`；最小工具结果；`P0`（闭环对照）；证据组同上。
6. **Creator 模式**；检查运行时状态、调试插件并创建预设模式；开发者工具；`A`；插件诊断和预设；`P2`；证据组同上。
7. **模型/供应商插件**；通过 provider 配置接入模型并可扩展自定义模型；用户指南；无工具副作用；模型配置；`P1`；证据组同上。
8. **工具插件**；文件、Shell、网络等能力由独立插件提供，可按模式安装或禁用；Cordis 插件系统；`A/X`；工具调用事件；`P0/P1`；证据组同上。
9. **Profile/子代理插件**；按 profile 安装或启用不同代理配置，可组合其他 coding agent；Web/CLI 配置；`A/X`；子代理会话和结果；`P2`；证据组同上。
10. **运行轨迹**；记录每次运行的工具调用、模型响应和结果，便于回放、搜索和调试；Web UI/日志；`R`；可检索轨迹；`P1`；证据组同上。
11. **预设模式和配置**；将一组模型、工具、提示和 UI 组合为可复用模式；配置/插件；`X`；模式定义；`P1`；证据组同上。
12. **快速演进边界**；项目处于 developer preview，明确提示可能发生兼容性破坏；README；`R`；版本风险；`P2`（不作为本题依赖）；证据组同上。

## 9.17 Replit Agent

官方证据组：[Replit Agent](https://docs.replit.com/replitai/agent)、[Plan mode](https://docs.replit.com/features/agent/plan-mode)、[App testing](https://docs.replit.com/features/agent/app-testing)、[Task lifecycle](https://docs.replit.com/features/agent/task-lifecycle)、[Skills](https://docs.replit.com/features/agent/skills)。

1. **自然语言建项目**；从想法创建项目、应用、设计、幻灯片或数据可视化，不要求用户先写代码；Project Editor Chat；`A/X/C`；项目文件和 artifact；`P2`（云端边界样本）；证据组同上。
2. **项目类型识别**；根据描述自动判断 web app、mobile app、slides、design 等类型，也允许用户选择；Project Editor；`X`；项目初始化配置；`P2`；证据组同上。
3. **代码与基础设施生成**；写代码、设置基础设施、配置数据库并测试结果；Agent build；`X/C`；代码、环境和测试日志；`P2`；证据组同上。
4. **Plan 模式**；在修改代码或数据前拆解有序任务、比较方案和权衡，用户审阅批准后开始构建；Plan 按钮或自然语言；`R/A`；可审阅任务列表；`P1`（设计启示）；证据组同上。
5. **持续测试与自修复**；Agent 定期测试自身工作，发现问题后接受自然语言反馈并修复；Agent 会话；`X/C`；测试结果和修复 diff；`P1`（闭环启示）；证据组同上。
6. **Checkpoints/回滚**；工作过程中建立 checkpoint，可回滚到历史状态；项目历史；`A`；恢复后的项目版本；`P1`；证据组同上。
7. **多 artifact 项目**；同一项目可共享后端和数据，同时包含 web app、mobile app、slides、video 等输出；Project；`X/C`；多个 artifact；`P2`；证据组同上。
8. **文件与文档产出**；生成 CSV、PDF、PowerPoint、Markdown 等文件；Agent；`A/X`；下载或项目内文件；`P2`；证据组同上。
9. **连接器查询**；从 BigQuery、Linear、Slack、Notion 等连接服务读取或操作数据；Connectors；`A/C`；外部数据和操作结果；`P2`；证据组同上。
10. **模式与付费审批**；Free/Power/Max/Turbo 选择模型能力，付费升级动作开始前需要用户确认；Agent modes；`A`；模式和计费确认记录；`P2`；证据组同上。
11. **后台任务与任务板**；运行后台任务、Kanban 计划和把变更应用回主版本；Tasks/Projects；`X/C`；任务状态和合并结果；`P2`；证据组同上。
12. **发布**；将构建的 artifact 一起发布，并提供应用访问能力；Publish；`A/C`；部署 URL、发布日志；`P2`；证据组同上。

## 9.18 产品逐条清单的共同结论

- 代理产品的共同工具原子不是“聊天”，而是**搜索/读取 -> 写入/补丁 -> 命令 -> 结果回传 -> 再次修复**。
- `Plan/Ask/Chat` 的实质是工具权限集合：成熟产品会在执行器层禁止写入/命令，而不是只在提示词中提醒模型。
- “支持测试”至少有三种含义：能生成测试、能运行测试、能读取失败并再次修改。只有最后一种才构成可靠闭环。
- “支持 Git”至少有三种含义：展示 diff、创建 commit、完成 branch/PR。个人 MVP 先实现前两种，避免把云端凭据和合并策略带入核心。
- 云端产品额外增加环境快照、依赖/secrets、异步日志、PR 权限和团队治理；这些是边界样本，不应误列为个人项目必做项。

# 十、能力原子与需求追踪矩阵

下表把逐产品清单压缩为可实现、可测试的功能原子。`市场证据`列只列本轮已明确支持该能力的代表产品；`验收标准`是项目可以在本地仓库中重复运行的行为，而不是宣传语。

| ID | 能力原子 | 市场证据（代表产品） | 本题级别 | 可执行验收标准 |
|---|---|---|---|---|
| F01 | 确定工作区根目录 | Claude、Codex、Cline、Aider、Continue | P0 | 启动时解析绝对路径，拒绝工作区外路径 |
| F02 | 文件列表/glob | Cursor、OpenCode、Cline、Continue | P0 | 给定 glob 返回排序后的相对路径 |
| F03 | 文本/正则搜索 | Claude、Cursor、Aider、OpenCode | P0 | 搜索返回文件、行号、截断片段 |
| F04 | 读取文件 | 全部本地代理 | P0 | 能读取 UTF-8 文件并限制大小 |
| F05 | 精确编辑 | Cursor、Cline、Continue Edit、JetBrains | P0 | 不匹配旧文本时拒绝写入并返回错误 |
| F06 | Patch 应用 | Codex、Cline、OpenCode | P0 | 支持 unified patch，失败不留下半写入文件 |
| F07 | 创建文件/目录 | Claude、Cursor、Cascade | P0 | 创建前检查路径和父目录，记录变更 |
| F08 | Shell 执行 | 几乎所有代理 | P0 | 返回 stdout/stderr/退出码，设置超时 |
| F09 | 测试/lint 命令 | Claude、Codex、Copilot、Aider | P0 | 至少执行用户指定或项目默认测试命令 |
| F10 | 错误上下文回传 | Continue、Cline、Claude、Cursor | P0 | 非零退出码自动形成下一轮模型上下文 |
| F11 | Tool calling 解析 | 所有 Agent harness | P0 | 处理合法调用、空响应、非法 JSON 和未知工具 |
| F12 | 循环终止/重试 | Claude、Codex、OpenCode | P0 | 最大轮数、重复调用、成功条件和人工中断均有效 |
| F13 | 命令/写入审批 | Cline、Cursor、Codex、Continue | P0 | 危险命令默认询问，拒绝后不执行 |
| F14 | 路径沙箱 | Codex、Devin CLI、Cursor | P0 | `..`、符号链接和工作区外写入被拦截 |
| F15 | 输出截断/上下文预算 | Aider repo map、Cursor、Continue | P0 | 单工具输出和总 prompt 都有硬上限 |
| F16 | 摘要/压缩/恢复 | Claude、Codex、OpenCode、Continue、Pi | P1 | 超预算后保留目标、变更、错误和待办摘要 |
| F17 | Plan/Act 状态机 | Cline、Cascade、OpenCode、Continue | P1 | Plan 状态调用写入/命令会被执行器拒绝 |
| F18 | Diff 展示 | Aider、Cursor、Cline、Codex | P0 | 完成后输出 unified diff 和文件清单 |
| F19 | Checkpoint/回滚 | Claude、Cursor、Cline、Aider、Replit Agent | P1 | 失败任务可恢复到执行前状态 |
| F20 | Git status/diff/commit | Claude、Aider、Copilot、Devin Review | P1 | 默认只读 status/diff，commit 需确认 |
| F21 | 项目规则文件 | `CLAUDE.md`、`AGENTS.md`、`.cursor/rules`、`.continue/rules` | P1 | 启动加载规则并在日志列出来源 |
| F22 | Slash command/Skill | Claude、Codex、Cline、Cascade、Continue | P1 | `/test` 或 `/review` 可展开为受控步骤 |
| F23 | MCP/外部工具 | Claude、Cursor、Cline、Continue、Cascade | P2 | 预留 ToolRegistry 接口，不作为 MVP 依赖 |
| F24 | 浏览器/截图 | Cursor、Claude、Cascade、Devin | P2 | 后续以独立 BrowserTool 接入，不能绕过审批 |
| F25 | 子代理 | Claude、Cursor、OpenCode、Cline、Devin | P2 | 后续限制独立上下文、权限和输出大小 |
| F26 | Worktree/并行 | Codex、Cursor、Cline、Zed、Devin | P1/P2 | 已有 workspace-local 创建/清理与 session owner；后续增加并行调度、冲突检测和合并策略 |
| F27 | CI/PR/云端交付 | Copilot、Codex cloud、Devin、Replit Agent | P2 | 本题仅保留可复制的 headless CLI 输出 |
| F28 | 日志/可观测性 | Devin Progress、Cline checkpoint、Codex logs | P0 | 每轮记录模型、工具、耗时、参数摘要、结果和错误 |

## 10.1 从市场全集到本题范围

**必须做（P0）**：F01-F15、F18、F28。它们共同覆盖题目明确要求的文件读写、命令执行、历史/上下文、工具定义与本地执行、模型输出解析、循环终止和错误处理；缺任何一个都可能出现“能调用模型但不能可靠完成任务”的断链。

**建议做（P1）**：F16、F17、F19-F22。它们分别解决长上下文、先计划后执行、失败恢复、可审查交付、项目约束和演示复现问题，代码量有限但显著提高可信度。

**明确后置（P2）**：F23-F25、F27。MCP、浏览器、子代理、云端和企业治理在市场上重要，但会引入新的协议、并发、凭据、网络和部署风险；worktree 已有安全的本地生命周期原型，自动并行调度仍后置，不让这些能力阻塞本地闭环。

## 10.2 需求到验收场景映射

| 场景 | 触发能力 | 通过条件 |
|---|---|---|
| 新增函数 | F02、F03、F04、F05/F06、F08 | Agent 找到相关文件，修改实现并运行测试 |
| 测试失败修复 | F08、F09、F10、F11、F12 | 读取 stderr/退出码，最多 N 次修复后得到通过或明确失败 |
| 危险命令 | F13、F14 | 删除/工作区外写入被询问或拒绝，日志包含决策 |
| 长任务 | F15、F16、F17 | 先输出计划，超预算后摘要，切换 Act 才能写入 |
| 可审查交付 | F18、F19、F20、F28 | 展示 diff、测试结果、日志，失败可回滚 |
| 项目约束 | F21、F22 | 读取规则文件，`/test` 或 `/review` 按模板执行 |

# 十一、实现边界与事实校正（面试口径）

1. **竞品是证据，不是依赖**：Pi、DSH、OpenCode、Claude Code、Codex、Cline 等只能用于功能对照；本题禁止在现成 agent 产品上包 UI，也禁止使用题目列出的 agent SDK/框架。实现应自行编写 AgentLoop、ToolRegistry、上下文和错误处理。
2. **不要承诺易变数字**：旧章节中的提供商数量、token 上限、版本号、价格和套餐属于易变信息；本深度章节只使用功能行为和官方文档链接作为依据。
3. **自动程度必须写清**：同一个“支持命令”可能是只生成命令、执行前询问、自动执行或云端执行；报告中的 `R/A/X/C` 不可互换。
4. **测试不是模型自评**：验收必须以真实命令退出码、stdout/stderr、diff 和可重复运行结果为准；模型说“已完成”不能替代验证器。
5. **安全边界优先于功能数量**：路径校验、命令审批、超时、输出截断、凭据脱敏和回滚是本项目的核心工程能力；MCP、浏览器和并行代理必须在这些边界之上扩展。

# 十二、调研完成检查表

- [x] 16 个代表性工具按统一模板逐条列出具体功能。
- [x] 每条清单包含行为、入口、自动程度、产出、优先级和官方证据组。
- [x] Continue、Devin、JetBrains、Gemini 的官方资料已补齐到 Agent/编辑/上下文/治理维度。
- [x] 市场能力已归并为 F01-F28 原子能力，并标注 P0/P1/P2。
- [x] 每个 P0/P1 能力都有可执行验收标准或演示场景。
- [x] 已明确竞品参考与本题自研实现的边界，避免误用现成 agent 框架。

## ForgeCode 当前差距复核（2026-08-30，v0.7.45）

本节以当前仓库源码、定向测试和正常 `fcc` 工作流为准；竞品能力只作为产品
形态基线，不把未能直接访问的页面当作已验证事实。OpenAI 官方 Codex 页面在
本轮环境返回 HTTP 403，因此不扩展 Codex 产品页面结论；Codex GitHub Contents API
可访问，审批 schema 证据已在下文单独记录。

| 领域 | ForgeCode 现状 | 主要差距 | 优先级 |
|---|---|---|---|
| 交互反馈 | 阶段标题、工具时间线、耗时、文件预览、红绿 diff、文本 delta、结果卡 | 仍可加强长任务状态聚合 | P0 |
| 工具协议 | 自研 schema、完整 JSON 校验、SSE 防重复/不完整调用，流中断有界重试；同轮全只读调用受控并行 | 更完整 capability negotiation/fallback | P1 |
| 上下文 | 仓库 map、引用解析、增量索引、压缩、有界历史；静态 definition/reference/hover 导航 | 仍缺少真正 LSP、跨语言精确解析和长期记忆 | P1 |
| 会话恢复 | JSONL、checkpoint、transaction、undo、tree/import、冲突检测；worktree 有界 owner 元数据与 reconcile | 缺少自动恢复/调度和后台多任务界面 | P1 |
| 安全 | Plan/Act/Bypass、启动信任、风险分类、审批、硬拦截、脱敏 | 不是操作系统级沙箱，需持续明确边界 | P0 |
| 验证 | 测试 profile、有限修复、review/export、轨迹评估 | 缺少语言服务和调试器集成 | P1 |
| 扩展发布 | Skills、hooks、SDK、JSONL RPC、工具收窄、uv/独立二进制布局 | 缺少 MCP、插件市场、跨平台一键安装 | P2 |

v0.7.41 进一步公开 RPC 安全协商信息：`rpc.describe` 明确列出 ForgeCode
实际支持的 `interactive/auto/deny` 模式（并提供 Codex `on-request/never` 的兼容映射）、`changes/execution/evidence` 三个
风险域，以及尚未实现的 Codex 风格 sandbox/rules/skill/request-permissions/MCP
域。该目录仅用于能力发现，不授予权限；实际 WorkspaceGuard、策略和审批仍在执行路径
上生效。这缩小了客户端“能发现什么”和“能授权什么”混淆的差距，但不宣称已实现
Codex granular approval。

截至当前 v0.7.40，正常交互工作流还提供 `/context`（有界索引健康度）和
`/events [limit] [kind]`（可筛选、带相对耗时和错误码的持久化事件尾部）。
这些能力不改变工具权限，只把已有审计证据暴露给用户；对应交互、机器契约和
provider 回归测试均已通过。

### 0.7.x 优先路线

1. **P0 可见闭环**：在普通 `fcc` 任务中持续输出经校验的文本、阶段/工具状态、
   真实改动文件、验证状态和耗时，不增加演示专用入口。
2. **P0 安全叙事**：让 workspace trust、审批预览、风险类别、拒绝结果和恢复
   状态出现在同一条时间线中。
3. **P1 可靠效率**：先定义事件顺序、取消、配额、错误聚合和 side-effect 禁止
   条件，再实现只读工具受控并行；写入、命令和事务默认保持串行。
4. **P1 工程上下文**：增加语言无关的符号概览和诊断入口，结果经过
   WorkspaceGuard、大小限制和脱敏，索引不承担授权职责。
5. **P2 生态能力**：worktree、多代理、MCP、浏览器/视觉、远程分享和一键安装
   后置，不挤占需求→修改→验证闭环。

### 0.7.2 实施审计（2026-08-30）

- **P0 可见闭环：已验证**。普通 `fcc` 已具备阶段、文本增量、工具时间线、
  真实改动文件、验证结果、耗时、会话指标、`/context` 和可筛选 `/events`。
- **P0 安全叙事：已验证**。workspace trust、Plan/Act/Bypass、审批、风险分类、
  拒绝结果、取消和 unresolved recovery 均有源码入口及回归测试；边界仍不是 OS
  sandbox，README 和交互说明已明确这一点。
- **P1 长任务可靠性：部分完成**。checkpoint、事件 JSONL、上下文压缩和流式协议
   重试已验证；同轮全只读工具已实现受控并行，worktree 已有受控生命周期和 session
   owner 记录，后台多任务界面与自动恢复仍未实现。
- **P1 工程上下文：部分完成**。仓库 map、增量索引、符号列表和 bounded 诊断可用；
  LSP 级 definition/reference/hover 仍是后续工作。

本审计以 `uv run pytest -rs` 的完整门禁（当前 525 passed、9 个 Windows symlink
条件 skip、2 warnings；早期 v0.7.0 记录为 485 passed）及 `uv run forgecode doctor`
输出为证据，不把未能访问的
Codex 官方页面或未实现的竞品特性当作本项目已完成能力。

### 0.7.1 实施审计：只读工具受控并行（2026-08-30）

- 同一模型响应中的调用仅在全部为无副作用工具时并行，线程上限为 4。
- 并行集合是显式白名单：`read_file`、`search`、`list_files`、
  `workspace_summary`、`repository_map`；其它工具即使声明无副作用也不自动加入。
- `write_file`、`apply_patch`、`run_command`、测试、事务及混合批次保持串行，
  不绕过审批、WorkspaceGuard、取消或 checkpoint。
- 结果按原始 tool-call 顺序回填，事件由主循环顺序写入，并记录
  `tool_batch_parallel` 证据事件。
- 取消时不再启动排队调用，并为每个未执行 call id 回填有界的
  `cancelled_before_start` tool message，避免协议配对错误。
- 定向回归覆盖并发耗时、call id 顺序和事件记录；完整门禁在发布前执行。

### 只读工具并行契约（现行）

- **范围**：仅允许 `read_file`、`search`、`list_files`、`workspace_summary` 和
  `repository_map`；每个调用必须通过现有 WorkspaceGuard、输出上限和取消令牌。
- **非目标**：不并行 `write_file`、`apply_patch`、`run_command`、测试、事务、hooks
  或任何 side-effecting tool；不改变 Plan/Act/Bypass 权限模型；不引入线程池以外的
  agent SDK 或外部调度服务。
- **完成条件**：同一模型响应中的全只读调用可受控并行；结果按原始 tool-call 顺序
  回填；每个调用仍有独立错误、超时和取消结果；总调用数、总输出和线程数有硬上限；
  session audit 事件保持单调序列。
- **失败语义**：单个只读调用失败不丢弃其它结果；聚合结果必须保留每个 call id；
  取消会阻止尚未开始的调用，并在事件中记录 `cancelled_before_start`；任何检测到
  side effect 的调用都回退为串行安全路径。
- **验证**：至少覆盖顺序稳定性、取消传播、配额、错误聚合、混合只读/副作用调用、
  checkpoint 一致性和 JSON/JSONL tool-call 配对；在此之前不标记 P1 并行为完成。

### 0.7.4 实施审计：provider capability negotiation（2026-08-30）

- **范围**：AgentLoop 在发送带工具 schema 的请求前读取 provider 的显式
  `capabilities` 声明；若声明 `tool_calling=false`，立即返回有界的
  `capability_mismatch`，并在审计事件中记录能力快照。
- **非目标**：不探测远端模型、不中途猜测模型能力、不改变未声明能力的
  兼容 provider 行为，也不引入新的 provider SDK。
- **完成条件**：不兼容请求不会到达 transport；错误可见、可恢复且不会产生
  tool-call；正常 provider 请求继续使用原有协议。
- **验证**：定向测试覆盖显式不支持工具调用的 provider；完整回归门禁在发布前执行。

### 0.7.5 实施审计：后台任务生命周期边界（2026-08-30）

- **范围**：后台进程最多 64 个活动任务；输出按字符计数硬上限，超长单行也会截断；
  状态返回 PID，完成后 duration 固定，便于长任务监控和审计。
- **非目标**：不提供跨进程任务恢复或 OS 级沙箱；任务仍属于当前进程的
  `ProcessManager`，跨会话持久化列入后续 worktree/后台任务路线。
- **完成条件**：输出、任务数量和时间字段均有界；poll 多次不会让完成时长继续增长；
  原有审批、风险分类和 kill 语义保持不变。
- **验证**：后台工具定向测试覆盖输出上限、截断标记和稳定完成 duration。

### 0.7.6 实施审计：后台任务发现（2026-08-30）

- 新增只读 `list_processes`，返回有界任务摘要、状态、PID 和 duration，省去用户
  记忆 task id；不返回捕获输出，避免重复上下文和敏感信息扩散。
- 定向测试覆盖任务发现、结构化结果和输出隔离；跨会话恢复仍列为后续差距。

### 0.7.7 实施审计：工具清单一致性（2026-08-30）

- `/tools` 的 human/machine 分类现在一致将 `list_processes` 标记为 read-only，
  避免新增工具只出现在 doctor 而在交互清单中被误分类。

### 0.7.8 实施审计：后台历史内存边界（2026-08-30）

- **范围**：`ProcessManager` 默认最多保留 256 条任务元数据；创建新任务时只
  淘汰已结束的最旧记录，活动任务不会被淘汰，避免长时间会话无界增长。
- **非目标**：不删除操作系统进程、不改变输出游标语义、不提供跨重启恢复。
- **验证**：定向测试覆盖历史上限和活动任务保护；后台工具现有测试继续通过。

### 0.7.9 实施审计：后台任务列表脱敏（2026-08-30）

- `list_processes` 仅返回 task id、状态、PID、退出码、duration、游标和截断标志，
  不返回命令或输出；从结构上避免列表接口扩散凭据。
- 定向测试覆盖命令参数不出现在 human/machine 结果中；具体命令仍只在启动调用的
  受控审计链路中按既有 secrets 脱敏策略处理。

### 0.7.10 实施审计：后台启动结果最小化（2026-08-30）

- `run_background` 成功结果只返回 task id 和状态，不把完整命令参数复制到模型
  context；任务列表同样不返回 command。
- 定向测试覆盖启动 metadata 不含 command，审批仍在工具边界执行。

### 0.7.11 实施审计：后台终止确认（2026-08-30）

- `kill_process` 在终止后最多等待 0.5 秒，并区分 `already_exited`、`confirmed` 和
  `unresolved`；只有确认退出才报告成功，避免长任务界面产生错误状态。
- 定向测试覆盖已启动进程的确认终止；超时路径保留 PID 和恢复所需元数据。

### 0.7.12 实施审计：工具能力计数可见性（2026-08-30）

- `/tools` human 输出显示 `Available tools (N)`，machine envelope 增加 `count`，
  两者都直接来源于当前注册/策略过滤后的工具集合。
- 该计数不授予新权限，也不暴露参数或凭据；定向 CLI 契约测试验证字段和分类同步。

### 0.7.13 实施审计：streaming capability negotiation（2026-08-30）

- **范围**：当 provider 显式 `stream_required=true` 且 capabilities 报告
  `streaming=false` 时，AgentLoop 在 transport 前返回 `capability_mismatch`。
- **非目标**：不探测远端能力、不改变 streaming=auto/on 的既有降级行为。
- **验证**：定向 AgentLoop 测试确认请求不会调用 provider completion，完整门禁后发布。

### 0.7.14 实施审计：SSE HTTP 能力回退（2026-08-30）

- **范围**：streaming=auto/on 遇到 SSE 端点 404/405/501 时，在同一有界请求流程内
  回退到 JSON completion，并记录 fallback attempt；`stream_required` 仍直接失败。
- **非目标**：不对任意 4xx/5xx 静默回退，不重复有副作用请求，不改变协议错误重试策略。
- **验证**：provider 定向测试覆盖 405→JSON 成功及 required streaming 严格失败。

### 0.7.15 实施审计：hooks 并行安全边界（2026-08-30）

- **范围**：当 ToolContext 配置 lifecycle hooks 时，AgentLoop 禁止同轮只读并行，
  回退到串行，以保证 before/after hook 顺序和共享状态一致。
- **非目标**：不改变无 hooks 场景的只读并行，不并行任何副作用工具，不引入锁来
  掩盖 hook 本身的非线程安全实现。
- **验证**：定向测试通过并发计数确认 hook-enabled 批次最大同时执行数为 1。

### 0.7.16 实施审计：CLI 风险组工具策略（2026-08-30）

- **范围**：`--tools` 与 `--exclude-tools` 支持 `read_only`、`changes`、
  `execution`、`evidence` 四个经过审计的风险组，并按当前工具注册表展开为
  稳定的精确工具名；展开后继续执行既有的未知、重复、重叠和注册表收窄检查。
- **非目标**：不改变 TOML 配置中仅允许精确工具名的语义；不声称实现 Codex
  granular approval profile，也不改变审批、WorkspaceGuard 或副作用执行边界。
- **受影响文件**：`src/forgecode/config.py`、`tests/test_v012_tool_policy.py`，
  以及版本和变更记录文件。
- **完成条件**：组名在 CLI 中可用，缺失工具安全忽略，策略结果可审计且顺序稳定。
- **验证**：工具策略定向测试 10 passed；发布前执行完整 pytest、doctor、compileall
  与 diff 检查。

### 0.7.17 实施审计：后台任务 stale 持久化（2026-08-30）

- **范围**：将后台任务的非敏感元数据写入 `.forgecode/background-tasks.json`；
  新进程加载仍在运行的记录时标记为 `stale`，并提供只读状态查询。
- **非目标**：不保存命令、输出或凭据；不恢复 PID、不自动重放命令，也不承诺跨主机恢复。
- **受影响文件**：`src/forgecode/tools/background.py`、`src/forgecode/tools/__init__.py`、
  `tests/test_background_tools.py` 及版本/变更记录。
- **完成条件**：状态文件有界、写入失败安全降级、重启记录不可执行且可审计。
- **验证**：后台工具定向测试 7 passed，compileall 与 diff 检查通过；发布前执行完整回归。

### 0.7.18 实施审计：风险域审批配置（2026-08-30）

- **范围**：新增 `[approval_scopes]` 配置表，支持 changes、execution、evidence
  三个风险域的 allow/ask/deny；默认空表保持旧版全局审批行为。
- **非目标**：不覆盖 Bypass 的显式跳过审批语义，不提供 OS sandbox，不宣称完整
  Codex granular profile；read-only 工具不会因该表获得额外副作用权限。
- **完成条件**：配置严格校验、策略在 chat 中生效、policy 诊断可见且凭据不外泄。
- **验证**：配置、策略与 CLI machine contract 定向测试通过；compileall 和 diff 检查通过。

### 审批可观测性补充（v0.7.18）

`RiskScopedApproval` 现在在审批事件中记录 `decision_source`，区分具体风险域的
scope allow/deny 与 fallback 全局策略。该字段只描述策略路径，不包含命令、内容或
凭据，便于 `/events` 和 JSONL 审计解释“为什么被允许或拒绝”。

### 0.7.20 实施审计：只读 worktree 发现（2026-08-30）

- **范围**：新增 `git_worktrees` 工具，调用 `git worktree list --porcelain`，返回
  经 WorkspaceGuard 校验的路径、分支和 HEAD，最多 64 条。
- **非目标**：不创建、切换、删除或合并 worktree，不提供并发隔离；这些仍是后续 P1/P2
  设计项。
- **验证**：工具策略定向测试、compileall 和 diff 检查通过。
- **修补**：将 `git_worktrees` 纳入 read_only 风险组及配置 known-tools 校验，避免
  新工具在不同策略入口出现可见性不一致。

### 0.7.21 实施审计：静态 symbol hover（2026-08-30）

- **范围**：新增 `symbol_hover`，返回符号定义行及最多 10 行邻近上下文；结果明确
  `precision=static`，不导入或执行项目代码。
- **非目标**：不声称实现 LSP hover/type inference，不启动语言服务器，不修改文件。
- **验证**：定向工具策略测试 17 passed，compileall 与 diff 检查通过。

### 0.7.22 实施审计：工具面板风险分组（2026-08-30）

- **范围**：人类 `/tools` 输出按 read_only、changes、execution、evidence 分组，
  并用 `!` 标记副作用工具；JSON/JSONL 输出契约保持不变。
- **非目标**：不改变工具注册、审批或实际权限，不新增专用演示入口。
- **验证**：CLI machine contract 与工具策略定向测试通过，compileall/diff 检查通过。

### 0.7.23 实施审计：RPC capability discovery（2026-08-30）

- **范围**：新增只读 `rpc.describe`，返回协议版本、方法列表、会话控制集合及
  WorkspaceGuard/审批/禁止自动重放等安全保证。
- **非目标**：不启动会话、不执行工具、不建立长期 daemon，不改变既有 JSONL 请求语义。
- **验证**：RPC/CLI machine contract 定向测试通过，compileall 和 diff 检查通过。

### 0.7.24 实施审计：嵌入式 capability discovery（2026-08-30）

- **范围**：Python embedding API 新增 `rpc_describe()`，复用 JSONL `rpc.describe`，
  并通过包级导出暴露。
- **非目标**：不启动 worker、不执行工具、不改变既有 session API。
- **验证**：embedding 与 CLI contract 定向测试通过，compileall 和 diff 检查通过。

### 0.7.25 实施审计：跨语言静态 hover 覆盖（2026-08-30）

- **范围**：`symbol_hover` 识别常见 JS/TS 箭头函数及 export variable 定义，仍返回
  有界上下文并标记 static precision。
- **非目标**：不引入解析器或 LSP，不推断类型，不执行源代码。
- **验证**：语义工具定向测试 18 passed，compileall 与 diff 检查通过。

### 0.7.26 实施审计：RPC capability replay 一致性（2026-08-30）

- **范围**：`rpc.describe` 现在将带 request id 的响应写入有界 replay/fingerprint 缓存，
  与其它 RPC 方法保持重复请求幂等及冲突检测语义。
- **非目标**：不改变 capability 内容、协议版本或会话执行行为。
- **验证**：CLI machine contract 定向测试 27 passed，compileall 与 diff 检查通过。

### 当前差距复核（v0.7.36）

基于已核实的 Codex `AskForApproval.ts`、Codex app-server 源码树、OpenCode
Tools/Permissions 文档和 Cline 工具/Plan 文档，ForgeCode 的优势是边界全部在本仓库
内、工具结果和审批事件可审计；仍有以下可验证差距：

| 能力 | ForgeCode 当前状态 | 优先级 |
| --- | --- | --- |
| 审批粒度 | 全局模式 + 可选 changes/execution/evidence scope，仍非 Codex granular | P1 |
| 语言服务 | 静态 definition/reference/hover；`lsp_status` 仅发现 PATH 可执行文件，非 LSP | P1 |
| 后台任务 | 有界生命周期、持久化 stale 元数据；不自动恢复执行 | P1 |
| 会话服务 | JSONL/RPC 可用；没有 Codex 等价的长期 daemon/app-server schema | P1 |
| 隔离执行 | WorkspaceGuard、审批边界和 workspace-local worktree 生命周期；不是 OS sandbox | P1 |
| 扩展生态 | skills/hooks 可用；无插件市场、MCP（按当前版本非目标） | P2 |

下一切片应优先评估真正 LSP 的安全适配、worktree 并行调度和长期 daemon；每个切片都必须
保留当前工具调用、审批、取消和 WorkspaceGuard 的安全契约。

### 审批策略实现（v0.7.18–v0.7.19）

新增 `RiskScopedApproval` 策略对象作为 Codex granular approval 的安全适配层原型：
它可对 `changes`、`execution`、`evidence` 风险域分别指定 `allow`、`ask` 或 `deny`，
未命中的工具始终委托给既有策略；审批事件记录 `decision_source`，便于审计解释。

### 外部资料复核（2026-08-30）

本轮（2026-08-30）重新请求 OpenCode Tools/Permissions 页面时均返回 HTTP 403，
因此不扩展其当前实现结论；Codex GitHub API 仍返回 HTTP 200，可继续使用已记录的
源码树与 `AskForApproval.ts` 证据。网络可访问性变化本身已记录，避免把旧页面状态
误写成当前可访问。

同日通过 GitHub Contents API 重新读取 Codex `AskForApproval.ts`（HTTP 200，blob
SHA `1d605501b2a3164d9effca75a6940d67ae833abb`）。当前 schema 仍明确包含
`untrusted`、`on-request`、`never` 与 granular 的 sandbox/rules/skill/request
permissions/MCP 五个布尔域；ForgeCode 的 `[approval_scopes]` 仍是风险域近似，
并不覆盖这些 Codex 专用维度。

本轮尝试重新读取 Codex recursive tree 时请求超时；仓库根 API 仍返回 HTTP 200。
因此继续沿用此前已成功获取的 app-server-client、app-server-daemon 和 schema
目录证据，不把本轮超时误写成目录删除或能力变化。

本轮再次请求 Cline 官方工具参考与 Plan/Act 文档，均返回 HTTP 200。资料继续确认
Cline 将工具结果回传到模型，并把 Plan 限制为探索/规划、Act 用于执行修改；这与
ForgeCode 当前 ToolRegistry、Plan/Act 边界一致。Cline 的浏览器、MCP 和 Kanban
worktree 并行能力仍属于 ForgeCode 的已知 P2/P1 差距，本轮未改变优先级。

### 最新门禁证据（v0.7.36）

- 完整回归：`521 passed, 9 skipped, 2 warnings`（3 分 13 秒）。skip 均为当前
  Windows 账户无法创建 symlink；warnings 为既有 pytest collection warning。
- `forgecode doctor` 显示 `lsp_status` 已注册，`python -m compileall -q src`、
  `git diff --check` 均通过。
- RPC/embedding/工具策略/后台任务/语义工具/只读并行的定向测试均在完整回归中覆盖；未发现
  JSONL、审批或 WorkspaceGuard 回归。

### v0.7.40 实施审计：RPC session state compatibility（2026-08-30）

- **范围**：RPC 恢复只接受 `idle/running/paused/completed/failed/cancelled/recovery_required`
  七种状态；未知或非字符串状态统一降级 `recovery_required`，避免未来 schema 值被误当成
  可运行 session。
- **非目标**：不猜测未知状态含义、不自动执行恢复、不改变合法状态的 TTL/replay/取消语义。
- **完成条件**：前向不兼容记录不会获得 active 权限；合法旧记录继续恢复。
- **验证**：RPC session lifecycle 定向测试 `52 passed`，compileall 与 diff 检查通过；
  完整发布门禁：`525 passed, 9 skipped, 2 warnings`（约 5 分 02 秒）；
  `uv run forgecode doctor`、compileall 与 `git diff --check` 均通过。

### v0.7.39 实施审计：pause/resume synchronization（2026-08-30）

- **范围**：AgentLoop 使用 `RLock` 保护 pause/resume/interative-control 状态在审批
  线程、同步工具边界和异步 loop 边界之间的读写；暂停等待循环在锁保护下读取最新状态。
- **非目标**：不改变暂停 API、取消语义或审批策略，不延长总任务超时，不提供强制终止
  任意第三方线程。
- **完成条件**：审批期间的暂停/恢复请求不会因未同步布尔状态而误判取消或绕过暂停；
  side effect 边界仍在审批后、执行前受控。
- **验证**：`tests/test_v010_interactive_controls.py` 5 passed，compileall 与 diff
  检查通过；完整门禁将确认共享核心回归。

修复后的发布门禁：`524 passed, 9 skipped, 2 warnings`（5 分 05 秒）。原先反复出现的
审批暂停竞态用例在该门禁中通过；skip 仍为 Windows symlink 能力限制，warnings 仍为既有
pytest collection warning。

### v0.7.38 门禁补充（2026-08-30）

- RPC session lifecycle 与 machine contract 定向测试通过；独立交互暂停测试通过。
- 两次完整回归均在既有 `test_pause_racing_approval_blocks_side_effect_until_resume`
  用例出现时序失败（测试的 2 秒 wait_for 触发 cancellation），其余测试通过；排除该
  单个已知竞态后完整集合通过。该结果按事实记录，不能视为 v0.7.38 的全绿门禁，后续
  需要单独收敛暂停/审批竞态后再重新执行发布级回归。

### 0.7.38 实施审计：workspace-local RPC session paths（2026-08-30）

- **范围**：恢复前要求 `session_path` 为无遍历组件的相对路径，解析后必须位于当前
  workspace 的 `.forgecode/sessions` 下并使用 `.jsonl` 后缀；越界或别名路径直接忽略。
- **非目标**：不读取/修复外部 session，不改变 session record TTL、replay、取消或进程
  stale 语义。
- **完成条件**：恢复流程不会因持久化记录把 session 读到 workspace 外；合法旧记录保持兼容。
- **验证**：RPC session lifecycle 定向测试 `52 passed`，覆盖 traversal/external path；
  compileall 与 diff 检查通过；发布门禁将补充完整回归。

### 0.7.37 实施审计：bounded RPC session recovery（2026-08-30）

- **范围**：RPC daemon 恢复 session record 前拒绝符号链接/junction，限制文件不超过
  512 KiB，并要求 workspace、mode、session_path 为字符串；异常记录安全忽略。
- **非目标**：不自动修复/删除损坏记录，不恢复运行中的进程，不改变 session TTL、取消或
  replay 语义。
- **完成条件**：恶意或异常大的恢复文件不会被加载，合法记录继续跨进程恢复。
- **验证**：RPC session lifecycle 与 machine contract 定向测试 `34 passed`，compileall
  与 diff 检查通过；发布门禁将补充 doctor 和完整回归。

### 0.7.36 实施审计：worktree reconcile observability（2026-08-30）

- **范围**：新增只读 `git_worktree_reconcile`，比较 `git worktree list --porcelain` 与
  ownership records，返回 `healthy`、`unmanaged`、`missing_path`、`owner_missing` 或
  `path_mismatch`；结果最多 64 条，不修改 Git 或 metadata。
- **非目标**：不自动修复/删除记录，不创建 worktree，不改变 owner 授权，不提供并行调度。
- **完成条件**：用户和 RPC 客户端能看见 worktree/session 状态不一致，而不会把发现动作
  当成授权或副作用操作。
- **验证**：工具策略与 RPC 契约定向测试 `52 passed, 1 skipped`，compileall 与 diff
  检查通过；发布门禁将补充 doctor 和完整回归。

补充审计：RPC capability catalog 通过测试与默认 registry 做名称集合等价校验，避免
新增内置工具只出现在本地 `/tools` 而无法被 SDK/RPC 客户端发现。该校验不授予权限，
仍保留运行时 policy 收窄边界。

### 0.7.35 实施审计：RPC tool capability discovery（2026-08-30）

- **范围**：`rpc.describe` 返回 31 项内置工具的名称、风险组和 side-effect 标记，
  与当前 registry 的核心工具集合保持稳定声明；增加 scope 说明，提醒客户端活动
  policy 仍可收窄工具，能力发现不等于授权。
- **非目标**：不从 RPC 授予工具、不绕过 WorkspaceGuard/审批、不声称反映工作区自定义
  插件或运行时过滤后的最终集合。
- **完成条件**：机器客户端无需猜测工具名即可构造能力面板，返回结构有界且可回放；
  JSONL RPC 与 embedding 语义保持不变。
- **验证**：`tests/test_cli_machine_contract.py` 定向测试 27 passed，compileall 与
  diff 检查通过；发布门禁将补充 doctor 和完整回归。

### 0.7.34 实施审计：bounded ownership metadata（2026-08-30）

- **范围**：ownership 文件读取先检查 256 KiB 大小上限，再解析 JSON；最多 64 条记录，
  每个名称、字段和值都经过字符/长度/schema 校验，异常统一转为
  `worktree_metadata_unavailable`。
- **非目标**：不修复不可信文件、不改变原子写入或 owner 校验、不提供 OS 级资源隔离。
- **完成条件**：异常大输入不会被完整加载，格式错误不会进入 Git 命令或 AgentLoop。
- **验证**：工具策略定向测试 `25 passed, 1 skipped`，compileall 与 diff 检查通过；
  发布门禁将补充 doctor 和完整回归。

### 0.7.33 实施审计：worktree metadata fail-closed errors（2026-08-30）

- **范围**：`git_worktrees` 与 `git_worktree_remove` 读取 ownership 文件时捕获
  WorkspaceGuard/文件格式异常，返回 `worktree_metadata_unavailable` 结构化错误；不把
  symlink/junction 或损坏 JSON 作为可执行状态继续处理。
- **非目标**：不自动修复或删除可疑状态文件，不改变 worktree owner 校验和原子写入语义。
- **完成条件**：错误经过 ToolRegistry 后保持 bounded output/metadata，AgentLoop 不因
  状态文件别名而崩溃。
- **验证**：定向工具测试 `24 passed, 1 skipped`，compileall 与 diff 检查通过。

### 0.7.32 实施审计：原子 worktree ownership 持久化（2026-08-30）

- **范围**：worktree ownership 状态写入使用同目录临时文件、`fsync`、`os.replace` 和
  进程内可重入锁；失败时删除临时文件，读者只会看到旧的完整 JSON 或新的完整 JSON。
- **非目标**：不提供跨进程分布式锁、不保证 OS/磁盘故障下的绝对持久性、不改变 owner
  校验或 Git worktree 生命周期语义。
- **完成条件**：并发调用不会交错写入，异常路径不留下可执行/敏感临时状态，已有 owner
  元数据格式保持兼容。
- **验证**：工具策略定向测试 `23 passed`，compileall 与 diff 检查通过；发布门禁将
  补充 doctor 和完整回归。

### 0.7.30 实施审计：worktree-session ownership（2026-08-30）

- **范围**：在受控 worktree 创建成功后写入 `.forgecode/worktrees.json`，记录最多 64
  条非敏感的名称、相对路径、分支和 `run_id`；列表工具展示已登记的 name/run_id；
  移除工具要求 owner 与当前会话一致，并在 Git 成功后删除记录。
- **非目标**：不恢复进程或会话、不自动切换/合并/推送分支、不把 metadata 当作授权
  替代品、不引入跨进程锁或 OS sandbox。
- **安全边界**：状态文件路径经过 WorkspaceGuard，内容不含命令、输出或凭据；损坏的
  状态文件安全降级为空记录；owner 不匹配在审批前失败。
- **验证**：工具策略定向测试 `23 passed`，覆盖 owner 写入、列表关联、owner mismatch
  拒绝与成功清理；compileall 与 diff 检查通过。发布门禁将补充 doctor 和完整回归。

### 0.7.29 实施审计：受控 worktree 生命周期（2026-08-30）

- **范围**：新增 `git_worktree_create` 与 `git_worktree_remove`，仅允许名称映射到
  `.forgecode/worktrees/<name>`；创建使用显式分支和可选 start point，移除仅作用于
  ForgeCode 管理目录。两者均是 changes 风险组的 side effect 工具。
- **非目标**：不自动切换主工作区、不合并或推送分支、不启动子代理、不提供 OS 沙箱，
  也不宣称已实现会话与 worktree 的自动绑定。
- **安全边界**：名称和 branch 经过长度/字符校验，目标由 WorkspaceGuard 解析；Plan
  模式拒绝；Act/Bypass 仍经过显式审批；移除不接受任意路径或任意目录。
- **验证**：定向工具与 AgentLoop 测试 `46 passed`，覆盖真实 Git 创建/移除、计划模式
  拒绝、危险名称拒绝；compileall 与 diff 检查通过。发布门禁将补充 doctor 和完整回归。

### 0.7.28 实施审计：LSP capability discovery（2026-08-30）

- **范围**：新增 `lsp_status` 只读工具，探测 PATH 上 Python、TypeScript、Rust、Go、
  Java 与 C/C++ 常见语言服务器，并返回结构化可用性结果；加入默认 registry、doctor
  清单和 AgentLoop 只读并行白名单。
- **非目标**：不启动语言服务器、不读取项目源码、不执行外部进程、不实现 LSP 协议，
  `supported: false` 明确表示 ForgeCode 尚未提供真正 LSP 集成。
- **安全边界**：仅调用 `shutil.which` 做 PATH 发现，参数 schema 无可写字段，经过现有
  ToolRegistry/WorkspaceGuard 入口，结果固定为六种语言且有界。
- **验证**：`tests/test_v012_tool_policy.py` 定向测试 19 passed；compileall 与
  `git diff --check` 通过。发布门禁将补充 doctor 与完整 pytest 结果。

这一步只缩小了“能力可发现性”差距；Codex/OpenCode 的真实语言服务、诊断和语义索引
仍是后续 P1 工作，不能将该工具描述为 LSP 实现。

### 0.7.27 实施审计：只读批次覆盖补齐（2026-08-30）

- **范围**：将 `git_worktrees` 与 `symbol_hover` 纳入 AgentLoop 的显式只读并行白名单，
  结果仍按模型调用顺序回填。
- **非目标**：不并行任何副作用工具；hooks 存在时仍强制串行；不改变取消、审批或
  WorkspaceGuard 语义。
- **验证**：AgentLoop 定向测试 24 passed，compileall 与 diff 检查通过。

- OpenCode 官方 Tools 页面当前可访问（HTTP 200），明确列出 `read`、`write`、
  `edit`、`bash`、`grep`、`glob` 以及 experimental `lsp`；这验证了 ForgeCode
  已有基础读写/搜索工具，但仍缺少真正语言服务器协议和更多编辑语义。
- OpenCode 官方 Permissions 页面当前可访问（HTTP 200），明确区分 `allow`、
  `ask`、`deny`，并说明 auto 模式不能绕过显式 deny；ForgeCode 的 Plan/Act/Bypass
  与审批策略覆盖相同安全意图，但配置表达式和外部工具粒度仍较少。
- OpenAI Codex CLI 官方页面在本环境仍返回 HTTP 403；本报告不声称读取其源码，
  Codex 差距判断仅依据此前可访问的官方文档索引和已记录的能力描述。
- Cline 官方工具参考与 Plan/Act 页面当前可访问（HTTP 200），明确说明模型调用
  可执行工具、结果回传，以及 Plan 只探索/规划、Act 执行修改；这与 ForgeCode
  的工具注册和模式权限方向一致。Continue 目标工具页本轮返回 HTTP 404，未将其
  作为已验证证据。
- OpenCode GitHub `packages/opencode/src/tool/tool.ts` 源码当前可直接访问（HTTP 200，
  约 6 KB），其工具层显式依赖 permission、session message 和 truncate 模块，
  说明成熟 harness 会把工具执行、权限决策、会话消息和输出截断拆成独立边界；
  ForgeCode 已有对应 ToolRegistry/ApprovalPolicy/SessionStore/输出上限，但尚未
  达到 OpenCode 那样的细粒度权限对象和 LSP 集成。
- OpenCode permission 源码本轮请求超时，不能声称已读取实现细节；仅保留官方权限
  文档的 allow/ask/deny 结论。
- OpenAI Codex GitHub 仓库 API 当前可访问（HTTP 200）。公开生成的
  `codex-rs/app-server-protocol/schema/typescript/v2/AskForApproval.ts` 明确定义
  `untrusted`、`on-request`、`never` 以及 granular 选项（sandbox、rules、skill、
  request permissions、MCP elicitations）。这是真实源码证据，说明 Codex 的审批
  边界比 ForgeCode 当前全局 interactive/auto/deny 更细；本项目后续可按风险域拆分，
  但不能宣称已实现 Codex 的 granular profile。
- 同一仓库的 recursive tree API 当前返回 7,629 个条目，包含独立的
  `app-server-client`、`app-server-daemon`、`app-server-protocol/schema/json`，以及
  多组 approval、permission、session、dynamic tool 的 JSON schema。这是源码树级
  证据，说明 Codex 的 RPC/客户端/后台守护进程是独立产品边界；ForgeCode 当前虽有
  JSONL RPC/Node embed，但尚无等价的长期 daemon 和生成式 schema 目录。针对这些
  README/具体 Rust 实现的单文件请求本轮超时或 404，未据此扩展结论。

### 0.7.3 实施审计：静态语义导航（2026-08-30）

- **范围**：新增 `find_definition` 与 `find_references`，覆盖常见源码后缀，
  返回路径、行号和截断行文本，并受 WorkspaceGuard、忽略规则及匹配上限约束。
- **非目标**：不声称提供 LSP 精度；不解析/执行项目代码，不引入语言服务器或
  第三方 agent SDK；复杂语法按“未找到”安全降级。
- **完成条件**：工具注册、schema、doctor 清单、模型可见工具和结构化结果一致；
  越界/忽略文件不可读取，结果有界且可审计。
- **验证**：定向测试覆盖 definition 行号、reference 数量和静态执行边界；
  发布前运行完整回归门禁。

每项实现都需要源码入口、定向测试、CLI 可观察证据和 changelog 条目；不以
“功能数量”替代可靠性、安全边界或可解释的失败结果。

v0.7.45 为 `session.events` 响应增加稳定 `event_id`、session 归属和
`schema_version`。事件 ID 使用 session handle + 单调 sequence，适合客户端断线
重连后的去重；该元数据不改变事件内容、权限或游标语义。

v0.7.44 为 RPC 客户端公开版本化 `event_schema`，列出当前 session 事件类型，并
声明未知类型前向兼容。客户端可先通过 `rpc.describe` 协商，再使用
`session.events` 的游标和过滤参数轮询；这仍不是 Codex 的无限推送通知协议。

### v0.7.43 实施审计：bounded RPC event long-poll（2026-08-30）

- **范围**：`session.events` 增加 0–30 秒 `wait` 参数，与 `after`、`type`、`limit`
  组合进行有界 long-poll；事件完成/失败持久化后唤醒等待者。
- **非目标**：不提供无限订阅、推送 socket、跨进程 daemon 或绕过现有 session 权限。
- **验证**：RPC session lifecycle 定向测试和完整发布门禁覆盖；空批次仍返回游标与截断信息。

### Codex app-server schema 复核（2026-08-30）

本轮通过 GitHub Contents API（HTTP 200）枚举 Codex app-server v2 的公开 schema
目录；其中可见 `Thread*`、`Turn*`、`CommandExecution*`、`FileChange*`、
`Reasoning*DeltaNotification`、`ContextCompactedNotification`、动态工具和多种
权限 profile 类型。这表明 Codex 的后台任务、实时通知与可恢复线程是协议层对象，
而不只是 CLI 文本。ForgeCode 当前 JSONL RPC 已有 session 事件、暂停/恢复/取消和
能力描述，但尚未提供等价的 thread/turn 通知 schema。后续应先定义稳定事件类型和
客户端订阅边界，再考虑长期 daemon，避免堆叠未经审计的后台功能。
v0.7.42 在此基础上为 `session.events` 增加有界 `type` 过滤；客户端可以用
`after + limit + type` 稳定轮询单类事件，并继续获得 `next_sequence`、
`oldest_sequence` 与 `truncated` 游标信息。这是向 Codex thread/turn 通知模型
迈出的兼容性步骤，但仍不是无限流式订阅或长期 daemon。
