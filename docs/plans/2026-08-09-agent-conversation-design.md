# Agent 对话功能补强设计（2026-08-09）

> 配套：brainstorming 流程产出；覆盖 `ops-agent-core` / `ops-agent-admin` / `ops-agent-front`。
> 目标：把现有「单轮任务派发 + 轮询」式 agent 对话，重做为**完整聊天体验**：思考过程展示（推理链 + 工具步骤）、SSE 流式回显、多轮长对话、历史会话列表与恢复。
> 阶段定位：**重新开发，旧的对话式 task 用法完全移除**；代码以最新状态为准，不向前兼容。

---

## 1. 关键决策（已与用户拍板）

| # | 议题 | 结论 |
|---|------|------|
| 1 | 会话模型 | **独立 conversation 体系**（conversations + messages 表 + 独立 API）；每轮用户提问 = 一条 user message + 一条内部 task；**旧「对话即任务」的接口/UI 移除** |
| 2 | 流式传输 | **SSE**（fetch + ReadableStream 消费，POST + Bearer token），admin 作为唯一对外口，不直接连 worker |
| 3 | 思考过程 | **推理链 + 工具步骤**：DeepSeek reasoner 的 `reasoning_content` 增量 + `tool_call`/`tool_result` 事件 |
| 4 | 历史恢复 | **会话列表 + 完整恢复**：标题/时间/删除，点击恢复完整消息流 |
| 5 | 任务体系 | **保留授权闭环**：对话负责问答，agent 写操作（执行命令/改配置）仍走 suggestion → 人工确认 → grantKey 闭环 |
| 6 | UI 形态 | **右抽屉内重做**（AgentAssistant.vue 两视图：会话列表 / 聊天） |

---

## 2. 数据模型

新增两张表（JPA `ddl-auto: update`，风格同现有实体）：

```
conversations(id UUID, title, user_id, created_at, updated_at)
messages(id UUID, conversation_id, role[user|assistant|system],
         content, reasoning, status[streaming|completed|failed],
         task_id nullable → 关联 agent_tasks, created_at)
```

- 会话归属用户，`user_id` 隔离，只能看自己的会话。
- 每轮用户提问 = 一条 user message + 一条关联 task 的 assistant message；`reasoning` 单独存推理链（可折叠展示），`content` 存最终答复（markdown 源文本）。
- 会话标题取第一轮用户消息前 20 字，之后可改。

---

## 3. REST API（admin 新增 `AgentConversationController`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/conversations` | 创建会话（返回 conversation） |
| GET | `/api/agent/conversations` | 会话列表（标题/更新时间/消息数，按更新时间倒序） |
| GET | `/api/agent/conversations/{id}/messages` | 恢复历史完整消息流 |
| DELETE | `/api/agent/conversations/{id}` | 删除会话（连带消息，best-effort 清理对应任务） |
| POST | `/api/agent/conversations/{id}/messages` | 发消息 → 建 user message + 派内部 task，返回 `{userMessage, taskId}` |
| POST | `/api/agent/conversations/{id}/stream` | SSE 流式（事件见下） |

**SSE 事件类型**（`event:` + `data:` JSON）：

| event | data 字段 | 说明 |
|---|---|---|
| `thinking` | `{delta}` | 推理链增量文本 |
| `tool_call` | `{name, args}` | 工具调用开始 |
| `tool_result` | `{name, summary}` | 工具结果摘要 |
| `delta` | `{delta}` | 答复增量文本 |
| `done` | `{messageId, content, reasoning}` | 该轮最终完整消息（兜底/重连用） |
| `error` | `{message}` | 出错，状态置 failed |

旧接口 `POST/GET /api/agent/tasks*` 的**对话用途移除**；task 仅作为授权闭环内部载体保留（`AgentTaskService.dispatch` 复用）。

---

## 4. 执行链路（前端 → admin → worker 流式回传）

```
前端 fetch /stream (SSE)
   │
   ▼
admin AgentConversationService
   ├─ 建 user message → AgentTaskService.dispatch(内部 task, 携带 conversationId + history)
   ├─ ConversationStreamManager(ConcurrentHashMap<conversationId, SseEmitter>) 挂接
   ▼
worker ops-agent-core graph.py
   ├─ state 初始化：history(前几轮 user/assistant 消息, token 上限截断) + 本轮 query
   ├─ model.astream(...) 逐 chunk
   │    ├─ reasoning_content → TaskEvent(thinking, delta)
   │    ├─ content         → TaskEvent(delta, delta)
   │    └─ tool_calls      → TaskEvent(tool_call) / tool_result
   └─ TaskResult.conclusion 落库（最终态）
   ▼
admin AgentGrpcService
   ├─ recordEvent 落 agent_events
   └─ → ConversationStreamManager → SSE 推前端
```

### 4.1 worker 侧（ops-agent-core）

- `graph.py` `agent_node()`：`ainvoke` → `astream`，拆 `AIMessageChunk.additional_kwargs["reasoning_content"]` 与 `content`。
- `TaskDispatch` 请求新增 `history` 字段（复用/扩展 proto：`repeated ChatMessage history` 或 JSON 字符串），graph 初始化时把 history 塞进 messages 开头。
- 事件流沿用现有 gRPC `TaskEvent`（event_type 扩展 `thinking`/`delta`/`tool_result`，`tool_call` 已有）。

### 4.2 admin 侧（Java）

- 新增 `ConversationStreamManager`：`ConcurrentHashMap<conversationId, SseEmitter>`，超时/异常清理。
- `AgentGrpcService` 收到 thinking/delta/tool_call/tool_result 时，除落 `agent_events` 外转发给对应 SSE 流；complete 时回推 `done` 事件并落 assistant message（content=conclusion, reasoning 聚合）。
- `AgentConversationService`：发消息 → 建 user message → 复用 dispatch（taskType 标记对话轮次，携带 conversationId + history）→ 返回 taskId。
- 历史恢复 `GET /messages` 直接查库，不经过 worker。
- 权限：conversation 归属 user，操作前校验 user_id（与现有鉴权风格一致）。

### 4.3 前端（ops-agent-front）

- `api/agent.js`：新增 conversation CRUD + `streamConversation()`（fetch + ReadableStream 解析 SSE，POST + Bearer）。
- `stores/agent.js`：重构为会话状态机（currentConversation / messages / streaming / abortController）。
- 发送消息 → 先本地渲染 user message → 打开 SSE 流 → 增量更新 assistant 消息（思考区实时滚动、答复 markdown 增量渲染、工具时间线）→ done 收尾。
- 断线/异常 → 状态 failed，保留已收内容，可「重试」拉取最终消息。

---

## 5. 前端 UI（抽屉内重做）

- **两视图**：会话列表（新建会话/标题/时间/删除/空态引导）⇄ 聊天视图（会话标题 + 返回列表）。
- **消息渲染**：新增依赖 `marked` + `highlight.js`；用户消息右对齐气泡，assistant 左对齐卡片；思考过程折叠面板（灰色等宽，推理链 + 工具调用时间线）；答复 markdown 渲染 + 代码高亮。
- **授权卡**：检测 suggestion 时内嵌「批准/拒绝」按钮（复用现有 approve/reject API），批准后轮询任务状态展示执行结果。
- **交互**：流式中禁发新消息，输入框变「停止」（前端断开 SSE + 任务置 cancel，worker 侧取消后置）；切换会话先停当前流再加载历史。
- **样式**：跨页面复用样式走 `plugins/vuetify.js` `defaults` / `styles/global.css`，组件只留强绑定 scoped 样式；Vuetify 4 规范（`useConfirm`/`useNotify`/MD3 typography）。

---

## 6. 测试与验收

- `ops-agent-test`：
  - `test_agent_conversation.py`：conversation CRUD + 权限隔离（动态建 READONLY 用户验 403）；
  - `test_agent_worker.py` 扩展：stream 事件序（thinking → tool_call → delta → done）断言；
  - 多轮：同会话连续两条消息，第二条 dispatch 的 history 含第一条（fake worker 校验）；
  - 历史恢复：新请求查 `/messages` 返回完整消息流。
- 手工验收：真实 DeepSeek 下观察推理链滚动、markdown 渲染、授权卡闭环、刷新页面会话列表恢复。

---

## 7. 实施顺序（建议）

1. proto 扩展（TaskDispatch.history + TaskEvent 类型）+ core `graph.py` 流式化
2. admin：实体/仓库 + Conversation API + ConversationStreamManager + gRPC 转发
3. 前端：api/store 重构 + AgentAssistant.vue 两视图重做 + marked 渲染
4. 测试 + 远端部署验证（走 GitHub push + deploy.sh）
