# Agent 消息存储重构：从 Java 流式落库迁移到 LangGraph 按轮持久化

- 日期：2026-08-10
- 状态：v1 设计稿

## 问题

当前消息存储由 Java（Admin）端在流式过程中逐行落库：

1. thinking/delta 事件 → `appendAssistantChunk()` 累积到内存 `AssistantRound`
2. tool_call 事件 → `flushAssistantRoundOnToolCall()` 落库 + `persistToolMessage()` 写 TOOL_CALL 行
3. tool_result 事件 → `persistToolMessage()` 写 TOOL_RESULT 行
4. 任务完成 → `finishAssistant()` 落最终消息

这种"流水账"模式的问题：

- Java 端在流式过程中做精细化的消息持久化，可能遗漏消息
- 两套消息管理逻辑（Agent 端 LangGraph 的 `add_messages` + Java 端 `AssistantRound`），职责不清
- 流式落库代码复杂，与 SSE 透传逻辑耦合

## 目标

- 消息存储主体移到 Agent（Python）模块，利用 LangGraph 每轮 LLM 循环后批量写入
- Java 端只做 SSE 透传（不落库）和审批消息写入
- 保留现有 `conversation_messages` 表结构不变，Agent 和 Admin 共享同一数据库
- 去掉 Java 端所有流式落库相关代码（`AssistantRound`、`appendAssistantChunk` 等）

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                   同一 PostgreSQL 实例                             │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  agent_conversation_messages                              │   │
│  │  ├── USER (Admin 写) / ASSISTANT (Agent 写)               │   │
│  │  ├── TOOL_CALL (Agent 写) / TOOL_RESULT (Agent 写)       │   │
│  │  └── APPROVAL (Admin 写)                                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  agent_tasks / agent_plans / agent_suggestions           │   │
│  │  (Agent 写)                                               │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
         │ gRPC 双向流                               │ JPA / REST
         ▼                                           ▼
  ┌──────────────────┐                  ┌──────────────────────────┐
  │  Agent (Python)  │                  │  Admin (Java)            │
  │                  │                  │                          │
  │  LangGraph 决策图 │◄─ gRPC 事件透传 ─│  SSE 流（纯转发，不落库）│
  │  MessageStore    │                  │  审批消息写入            │
  │  (asyncpg 写消息) │                  │  JPA 历史查询            │
  └──────────────────┘                  └──────────────────────────┘
```

## 改动清单

### 1. Agent 端新增 `MessageStore`（`app/agent/message_store.py`）

新增 `app/agent/message_store.py`，通过 asyncpg 操作 `conversation_messages` 表。

**核心方法：**

```python
class MessageStore:
    def __init__(self, db: Database): ...

    async def save_round(self, conversation_id: str, task_id: str,
                         assistant: AIMessage,
                         tool_calls: list[dict],
                         tool_results: list[ToolMessage]) -> None:
        """每轮 LLM 循环完成后调用。
        - assistant: 本轮 LLM 产出的 AIMessage（含 reasoning_content）
        - tool_calls: [{"id","name","args"}]（可能有多个并行调用）
        - tool_results: [ToolMessage]（每个 tool_call 对应一个）
        - USER 消息由 Admin 在 send() 时通过 JPA 写入，Agent 不写 USER
        """

    async def get_messages(self, conversation_id: str) -> list[dict]:
        """按 id 升序返回会话全部消息。"""

    async def delete_messages(self, conversation_id: str) -> None:
        """删除会话消息（删除会话时联动）。"""
```

**`save_round` 写入规则：**

| 消息 | 行数 | 字段填充 |
|------|------|---------|
| AIMessage (有 tool_calls) | 1 行 ASSISTANT | `content`, `reasoning`, `status=completed` |
| 每个 tool_call | 1 行 TOOL_CALL | `tool_call_id`, `tool_name`, `tool_args`, `content` |
| 每个 tool_result | 1 行 TOOL_RESULT | `tool_call_id`, `tool_name`, `tool_summary`, `content` |
| AIMessage (无 tool_calls) | 1 行 ASSISTANT | `content`, `reasoning`, `status=completed` |

**消息 ID 生成规则：**

```
user_${task_id}           → USER 消息
round_${task_id}_${n}     → ASSISTANT 消息（第 n 轮）
tc_${tool_call_id}        → TOOL_CALL 消息
tr_${tool_call_id}        → TOOL_RESULT 消息
```

### 2. `graph.py` 集成（写入点）

**写入点 1：`tools_node` 末尾**（有工具调用的轮次）

```python
async def tools_node(state):
    # ... 现有工具执行逻辑 ...

    # 本轮消息持久化
    if store is not None and store.enabled and ctx.conversation_id:
        # 从 state 中找到本轮新增的 assistant 消息
        # 工具执行结果已经在 tool_msgs 中
        await store.save_round(ctx.conversation_id, ctx.task_id,
                                assistant_msg, tool_calls, tool_msgs)

    return {"messages": ..., "pending_tools": [], "pending_approval": ...}
```

**写入点 2：`agent_node` 中检测到最终轮**（无工具调用）

```python
async def agent_node(state):
    # ... LLM 调用 ...

    if not tool_calls and not state.get("pending_approval"):
        # 最终轮，无工具调用 → 保存最终 assistant 消息
        if store is not None and store.enabled and ctx.conversation_id:
            await store.save_round(ctx.conversation_id, ctx.task_id,
                                    merged, [], [])

    return {"messages": [merged], "pending_tools": pending}
```

### 3. `core.py` 变化

- `handle_dispatch` 和 `handle_execute` 中：消息写入由 `graph.py` 的 `save_round` 覆盖
- 移除 `_extract_reasoning` 兜底写入（因为消息已由按轮写入覆盖，不再需要单独兜底行）
- USER 消息已由 Admin 在 `send()` 时通过 JPA 写入，无需 Agent 额外处理

### 4. 数据库配置

`MessageStore` 复用 `TaskStore` 的 `Database` 实例（同一个 asyncpg 连接池）。

### 5. Java 端移除清单

**`AgentConversationService` 中移除：**

```java
// 删除整个 AssistantRound 机制
private final ConcurrentHashMap<String, AssistantRound> assistantRounds;  // ✗
private static final class AssistantRound { ... }                         // ✗

// 删除以下方法
public void appendAssistantChunk(...)         // ✗
public void flushAssistantRoundOnToolCall(...) // ✗
public void finalizeAssistantOnError(...)      // ✗
private void persistAssistantRound(...)        // ✗
public void upsertToolCallRow(...)            // ✗
```

**`AgentGrpcService` 中移除/简化：**

```java
// handleEvent 简化：
// - thinking/delta → 只 forwardStreamEvent，不调 appendAssistantChunk
// - tool_call/tool_result → 只 forwardStreamEvent，不调 flushAssistantRound + persistToolMessage
// - 删除 persistToolMessage() 方法

// handleResult 简化：
// - 只推 SSE done 事件，不调 conversationService.finishAssistant()
// - 保持 refreshApprovalAfterExecuteTask
```

**`finishAssistant` 简化：**

```java
public void finishAssistant(String conversationId, String taskId,
                            boolean ok, String conclusion) {
    if (conversationId == null || conversationId.isBlank()) {
        streamManager.unbindTask(taskId);
        return;
    }
    // 只推 done 事件，不存消息
    Map<String, Object> done = new LinkedHashMap<>();
    done.put("taskId", taskId);
    done.put("status", ok ? "completed" : "failed");
    done.put("content", conclusion);
    streamManager.push(conversationId, "done", done);
    streamManager.unbindTask(taskId);
}
```

### 6. Java 端保留的代码

- `saveApprovalDecision()` — 审批消息写入（APPROVAL 行）
- `savePlanUpdateMessage()` — plan 更新消息
- SSE 转发 `forwardStreamEvent()`
- 会话 CRUD（`create`, `list`, `messages`, `delete`）
- `buildHistory()` — 历史消息组装

### 7. 完整数据流

```
1. 用户发消息 → Admin REST API → Admin JPA 落 USER 消息 → 发 TaskDispatch(gRPC)
2. Agent 收到 TaskDispatch → 从 conversation_messages 加载历史消息（Admin 已写入 USER 行）
3. Agent 构建 LangGraph 初始状态 → 运行决策图
4. 每轮 LLM 循环：
   a. agent_node: LLM 流式产出 → 事件经 gRPC → Admin SSE 透传 → 前端
   b. tools_node: 执行工具 → 实时发 tool_call/tool_result 事件 → SSE 透传
   c. tools_node 末尾: MessageStore.save_round() 持久化本轮消息
5. 最终轮（无工具调用）→ MessageStore.save_round() 持久化最终消息
6. TaskResult → Admin 推 SSE done 事件
7. 前端收到 done → 加载完成
```

### 8. 边界与错误处理

| 场景 | 处理方式 |
|------|---------|
| Agent 消息写入失败 | 不阻塞任务继续（`log.warning`），消息由下次任务从 checkpoint 恢复 |
| 任务中途取消 | 已写入的消息保留，未写入的丢失（等价于"未完成"） |
| Java 端 SSE 连接断开 | 不影响 Agent 端消息写入，前端重连后重新查询历史 |
| 审批消息写入失败 | Java 端 `@Transactional` 回滚，不影响 Agent 端消息 |
| 历史消息查询 | Admin 直接 JPA 查 `conversation_messages`，按 `id` 升序返回 |