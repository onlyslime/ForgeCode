# ForgeCode v0.0.13 harness slice

本阶段聚焦完整 CLI harness：provider/profile 与环境变量凭据引用、JSONL RPC
程序化调用、取消/暂停边界、工作区 trust，以及 offline/telemetry 隐私策略。
凭据只保存环境变量名；`trust grant|revoke|status` 的记录位于被忽略的
`.forgecode/trust.json`，不包含工作区内容。`sdk/node/index.mjs` 提供 Node
客户端，调用与 CLI 相同的 JSON envelope。TypeScript 插件、富交互 UI、Web/
桌面/IDE 不在本阶段范围内。

交互会话支持 `/login`（只显示当前 profile 的环境变量引用）和 Escape 控制字节
即时取消。对需要显式安全门禁的自动化流程，可使用 `run --require-trust`；未
授权工作区返回 `trust_required`，授权记录可用 `trust revoke` 撤销。RPC 服务
逐行转发事件及最终结果，断连不会重放副作用。

RPC 请求可携带有界 `id` 与显式方法名（如 `provider.list`、`config.show`、
`trust.status`、`doctor`）；旧客户端继续使用 `argv` 数组。

`run` 方法接受有界 `params.prompt` 以及 workspace、mode、session、profile、
auto_approve、require_trust 参数，输出与 CLI 完全相同的事件 envelope。

Python `EmbeddedSession` 与 Node `interactive()` 可驱动生产 chat worker，并通过
同一输入通道发送消息、pause/resume/cancel/quit 控制命令。

当真实 provider 已配置时，Act 模式自动要求 trust；框架离线/未配置模式保留
旧版只读与测试兼容性，亦可用 `--require-trust` 显式启用门禁。
