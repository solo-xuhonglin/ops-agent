# Agent 消息存储重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将消息存储主体从 Java 流式落库迁移到 Agent 端 LangGraph 按轮持久化

**Architecture:** Agent 新增 `MessageStore` 类，在每轮 LLM 循环完成后异步写入 `conversation_messages` 表（共享数据库）。Java 端去掉所有流式落库代码（`AssistantRound`、`appendAssistantChunk` 等），只做 SSE 透传和审批消息写入。

**Tech Stack:** Python (asyncpg, LangGraph), Java (Spring Boot JPA), PostgreSQL

---

### Task 1: 创建 MessageStore（Agent 端）

**Files:**
- Create: `ops-agent-core/app/agent/message_store.py`
- Test: `ops-agent-core/app/tests/test_message_store.py`

- [ ] **Step 1: 编写 MessageStore 类**

```python
"""Agent 对话消息持久化（增量插入 conversation_messages 表）。

每轮 LLM 循环完成后批量写入，不做流式逐 token 落库。
Agent 写 ASSISTANT/TOOL_CALL/TOOL_RESULT 行，
Admin 写 USER/APPROVAL 行（JPA）。
"""
import json
import logging
import uuid
from typing import Any, Optional

from langchain_core.messages import AIMessage, ToolMessage

from app.db import Database

log = logging.getLogger("message_store")

# 消息 ID 前缀
_ID_USER = "user_"
_ID_ROUND = "round_"
_ID_TC = "tc_"
_ID_TR = "tr_"


class MessageStore:
    """对话消息持久化（增量插入，每轮循环后写入）。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    @property
    def enabled(self) -> bool:
        return self.db.enabled

    async def save_round(self, conversation_id: str, task_id: str,
                         round_index: int,
                         assistant: AIMessage,
                         tool_calls: list[dict],
                         tool_results: list[ToolMessage]) -> None:
        """保存一轮 LLM 循环产生的消息到 conversation_messages 表。

        - assistant: 本轮 LLM 产出的 AIMessage（含 reasoning_content）
        - tool_calls: [{"id","name","args"}] (可能有多个并行调用)
        - tool_results: [ToolMessage] (每个 tool_call 对应一个)
        - round_index: 当前轮次序号（从 0 开始）
        - USER 消息由 Admin 在 send() 时通过 JPA 写入，Agent 不写 USER
        """
        if not self.enabled:
            return
        if not conversation_id:
            return

        # 1. ASSISTANT 行
        assistant_id = f"{_ID_ROUND}{task_id}_{round_index}"
        content = assistant.content or ""
        reasoning = ""
        if assistant.additional_kwargs:
            reasoning = assistant.additional_kwargs.get("reasoning_content") or ""
        kwargs = {
            "message_id": assistant_id,
            "conversation_id": conversation_id,
            "kind": "ASSISTANT",
            "role": "assistant",
            "content": content,
            "reasoning": reasoning,
            "status": "completed",
            "task_id": task_id,
        }
        await self._upsert(kwargs)

        # 2. TOOL_CALL 行（每个 tool_call 一行）
        for tc in tool_calls:
            call_id = tc.get("id", "")
            tc_id = f"{_ID_TC}{call_id}"
            tc_args = json.dumps(tc.get("args", {}), ensure_ascii=False)
            tc_kwargs = {
                "message_id": tc_id,
                "conversation_id": conversation_id,
                "kind": "TOOL_CALL",
                "role": "tool",
                "content": f"调用工具 {tc.get('name', '')}",
                "status": "completed",
                "task_id": task_id,
                "tool_call_id": call_id,
                "tool_name": tc.get("name", ""),
                "tool_args": tc_args,
            }
            await self._upsert(tc_kwargs)

        # 3. TOOL_RESULT 行（每个 tool_result 一行）
        for tr in tool_results:
            tr_call_id = getattr(tr, "tool_call_id", "") or ""
            tr_id = f"{_ID_TR}{tr_call_id}"
            # 从 content 解析摘要（JSON 字符串，截取前 500 字符）
            tr_content = str(tr.content or "")[:500]
            tr_kwargs = {
                "message_id": tr_id,
                "conversation_id": conversation_id,
                "kind": "TOOL_RESULT",
                "role": "tool",
                "content": tr_content,
                "status": "completed",
                "task_id": task_id,
                "tool_call_id": tr_call_id,
                # tool_name 从 tool_call 配对（TOOL_CALL 行的 tool_name）
                "tool_summary": tr_content,
            }
            await self._upsert(tr_kwargs)

    async def get_messages(self, conversation_id: str) -> list[dict]:
        """按 id 升序返回会话全部消息行。"""
        if not self.enabled or not conversation_id:
            return []
        try:
            return await self.db.fetch(
                "SELECT * FROM conversation_messages "
                "WHERE conversation_id=$1 ORDER BY id ASC",
                conversation_id)
        except Exception as e:
            log.warning("get_messages failed: %s", e)
            return []

    async def delete_messages(self, conversation_id: str) -> None:
        """删除会话消息（删除会话时联动）。"""
        if not self.enabled or not conversation_id:
            return
        try:
            await self.db.execute(
                "DELETE FROM conversation_messages WHERE conversation_id=$1",
                conversation_id)
        except Exception as e:
            log.warning("delete_messages failed: %s", e)

    async def _upsert(self, kwargs: dict) -> None:
        """按 message_id 幂等插入/更新一行。"""
        if not self.enabled:
            return
        columns = ", ".join(kwargs.keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(kwargs)))
        updates = ", ".join(f"{k}=${i+1}" for i, k in enumerate(kwargs.keys()))
        values = list(kwargs.values())
        sql = (
            f"INSERT INTO conversation_messages ({columns}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (message_id) DO UPDATE SET {updates}"
        )
        try:
            await self.db.execute(sql, *values)
        except Exception as e:
            log.warning("message upsert failed: %s msg=%s", e, kwargs.get("message_id", "")[:20])
```

- [ ] **Step 2: 编写 MessageStore 测试**

```python
"""测试 MessageStore 的写入和读取逻辑。"""
import pytest
from app.agent.message_store import MessageStore
from langchain_core.messages import AIMessage, ToolMessage


@pytest.mark.asyncio
async def test_save_round_assistant_only(fake_db):
    store = MessageStore(fake_db)
    await store.save_round(
        conversation_id="conv_1",
        task_id="task_1",
        round_index=0,
        assistant=AIMessage(content="Hello"),
        tool_calls=[],
        tool_results=[],
    )
    rows = await store.get_messages("conv_1")
    assert len(rows) == 1
    assert rows[0]["kind"] == "ASSISTANT"
    assert rows[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_save_round_with_tools(fake_db):
    store = MessageStore(fake_db)
    await store.save_round(
        conversation_id="conv_1",
        task_id="task_1",
        round_index=0,
        assistant=AIMessage(content="", tool_calls=[{"id": "call_1", "name": "training_get", "args": {"jobId": 1}}]),
        tool_calls=[{"id": "call_1", "name": "training_get", "args": {"jobId": 1}}],
        tool_results=[ToolMessage(content='{"status": "RUNNING"}', tool_call_id="call_1")],
    )
    rows = await store.get_messages("conv_1")
    assert len(rows) == 3  # 1 ASSISTANT + 1 TOOL_CALL + 1 TOOL_RESULT
    kinds = [r["kind"] for r in rows]
    assert kinds == ["ASSISTANT", "TOOL_CALL", "TOOL_RESULT"]


@pytest.mark.asyncio
async def test_delete_messages(fake_db):
    store = MessageStore(fake_db)
    await store.save_round("conv_1", "task_1", 0, AIMessage(content="Hi"), [], [])
    await store.delete_messages("conv_1")
    rows = await store.get_messages("conv_1")
    assert len(rows) == 0
```

- [ ] **Step 3: 创建 FakeDB 测试夹具**

```python
"""app/tests/conftest.py 或 test_message_store.py 内"""
import pytest
from app.db import Database


@pytest.fixture
async def fake_db():
    """返回一个内存模式的 Database 实例（使用 SQLite 或其他方式）。
    
    由于 asyncpg 需要真实 PG 连接，使用 mock 模式。
    """
    db = Database()
    # 使用内存 dict 模拟
    db._mock_store = {}
    db._mock_enabled = True
    
    original_execute = db.execute
    original_fetch = db.fetch
    
    async def mock_execute(sql, *args):
        pass  # 简化：真正的测试可以 mock
    
    async def mock_fetch(sql, *args):
        return []
    
    db.execute = mock_execute
    db.fetch = mock_fetch
    db.enabled = True
    
    yield db
```

- [ ] **Step 4: 验证测试通过**

Run: `cd /workspace/ops-agent-core && python -m pytest app/tests/test_message_store.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add ops-agent-core/app/agent/message_store.py ops-agent-core/app/tests/test_message_store.py
git commit -m "feat(agent): add MessageStore for per-round message persistence"
```

---

### Task 2: 集成 MessageStore 到 graph.py（Agent 端）

**Files:**
- Modify: `ops-agent-core/app/agent/graph.py`

- [ ] **Step 1: 在 tools_node 末尾添加 save_round 调用**

在 `graph.py` 的 `tools_node` 函数末尾，`return` 之前插入：

```python
        # 本轮消息持久化（仅对话任务，有工具调用时）
        if msg_store is not None and msg_store.enabled and ctx.conversation_id:
            try:
                assistant_msg = None
                for m in reversed(state.get("messages") or []):
                    if getattr(m, "type", "") == "ai":
                        assistant_msg = m
                        break
                if assistant_msg is not None:
                    round_index = state.get("_round", 0)
                    await msg_store.save_round(
                        conversation_id=ctx.conversation_id,
                        task_id=ctx.task_id,
                        round_index=round_index,
                        assistant=assistant_msg,
                        tool_calls=state.get("pending_tools") or [],
                        tool_results=tool_msgs,
                    )
            except Exception as e:
                log.warning("save_round failed (non-blocking): %s", e)
        # 递增轮次
        current_round = state.get("_round", 0)
        update["_round"] = current_round + 1
```

**注意：** `msg_store` 是 `MessageStore` 实例，通过 `build_graph` 闭包传入（与 `store=TaskStore` 并列）。`_round` 计数器在 `tools_node` 返回时递增，`agent_node` 最终轮时用当前 `_round` 保存后递增。

- [ ] **Step 2: 在 AgentState 中添加 `_round` 字段**

```python
class AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    ctx: TaskContext
    pending_tools: Optional[list[dict]]
    pending_approval: bool
    _round: int  # 当前轮次序号（递增，用于消息 ID 生成）
```

- [ ] **Step 3: 在 agent_node 中读取 `_round`，在 tools_node 中递增**

`agent_node` 读取 `_round` 但不递增（tools_node 和最终轮保存后递增）：

```python
async def agent_node(state: AgentState) -> dict[str, Any]:
    # ... 现有代码 ...
    
    current_round = state.get("_round", 0)  # 读取当前轮次，不递增
    
    # ... LLM 调用 ...
    
    # 最终轮（无工具调用）保存消息并递增
    if not tool_calls and not state.get("pending_approval"):
        if msg_store is not None and msg_store.enabled and ctx.conversation_id:
            try:
                await msg_store.save_round(
                    conversation_id=ctx.conversation_id,
                    task_id=ctx.task_id,
                    round_index=current_round,
                    assistant=merged,
                    tool_calls=[],
                    tool_results=[],
                )
            except Exception as e:
                log.warning("save_round final failed: %s", e)
    
    return {"messages": [merged], "pending_tools": pending,
            "_round": current_round + 1}  # 最终轮递增
```

`tools_node` 中保存当前轮后递增：

```python
# 在 tools_node 末尾，保存消息后递增 _round
current_round = state.get("_round", 0)
# ... save_round(round_index=current_round) ...
update["_round"] = current_round + 1
```

- [ ] **Step 4: 更新 `run_graph` 初始 state**

```python
async def run_graph(graph: Any, ctx: TaskContext,
                    messages: list[Any], max_rounds: int = 10) -> tuple[list[Any], bool]:
    config = {
        "configurable": {"thread_id": ctx.task_id},
        "recursion_limit": max_rounds * 4 + 16,
    }
    try:
        result = await graph.ainvoke(
            {"messages": messages, "ctx": ctx, "pending_tools": [],
             "pending_approval": False, "_round": 0},  # 添加 _round
            config=config)
        return result["messages"], False
    except GraphRecursionError:
        ...
```

- [ ] **Step 5: 更新 `build_graph` 函数签名**

在 `build_graph` 参数列表中添加 `msg_store: Optional[MessageStore] = None`，闭包传给 `tools_node` 和 `agent_node`：

```python
def build_graph(llm_runtime, http, registry, client,
                tracker=None, store=None, msg_store=None):
```

确认 `msg_store` 被 `tools_node` 和 `agent_node` 的闭包正确捕获。

- [ ] **Step 6: Commit**

```bash
git add ops-agent-core/app/agent/graph.py
git commit -m "feat(agent): integrate MessageStore save_round into graph nodes"
```

---

### Task 3: 集成 MessageStore 到 core.py 和 main.py

**Files:**
- Modify: `ops-agent-core/app/agent/core.py`
- Modify: `ops-agent-core/app/main.py`

- [ ] **Step 1: 移除 core.py 中的 `_extract_reasoning` 兜底**

在 `handle_dispatch` 中，移除 `_extract_reasoning(final_messages)` 的兜底写入逻辑。消息已由 `graph.py` 的 `save_round` 覆盖，不需要额外兜底。

移除以下代码块：

```python
# 以下全部移除，消息已由 graph.py 按轮写入
if store is not None and store.enabled:
    try:
        await store.finish_task(ctx.task_id, "SUCCEEDED", conclusion,
                                _extract_reasoning(final_messages))
    except Exception as e:
        log.warning("task finish persist failed: %s", e)
```

以及 `handle_execute` 中类似的 `store.finish_task` 调用。

- [ ] **Step 2: 简化 core.py 的 handle_dispatch 收尾**

```python
# 只保留 send_result，不再调用 store.finish_task（消息由 graph.py 按轮写入）
await client.send_result(ctx.task_id, ok=True, conclusion=conclusion,
                         reasoning=_extract_reasoning(final_messages))
```

- [ ] **Step 3: 在 main.py 中装配 MessageStore**

```python
from app.agent.message_store import MessageStore

# 在 amain() 中，创建 store 之后：
store = TaskStore(db, cfg.worker_id)
msg_store = MessageStore(db)  # 新增

# 传入 core.handle_dispatch 和 graph.build_graph
```

更新 `core.handle_dispatch` 签名，添加 `msg_store` 参数，透传给 `build_graph`。

- [ ] **Step 4: 更新 build_graph 调用链**

`core.py` -> `build_graph(llm_runtime=..., store=store, msg_store=msg_store)` -> `graph.py` 的 `build_graph` 接收 `msg_store` 闭包。

- [ ] **Step 5: Commit**

```bash
git add ops-agent-core/app/agent/core.py ops-agent-core/app/main.py
git commit -m "feat(agent): wire MessageStore into core and main"
```

---

### Task 4: Java 端清理 - AgentConversationService

**Files:**
- Modify: `ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentConversationService.java`

- [ ] **Step 1: 移除 AssistantRound 相关字段和内部类**

```java
// 删除以下字段
private final ConcurrentHashMap<String, AssistantRound> assistantRounds = new ConcurrentHashMap<>();

// 删除整个内部类
private static final class AssistantRound {
    final String messageId = UUID.randomUUID().toString();
    final StringBuilder reasoning = new StringBuilder();
    final StringBuilder content = new StringBuilder();
    boolean hasData = false;
}
```

- [ ] **Step 2: 移除流式落库方法**

```java
// 删除以下方法
public void appendAssistantChunk(String taskId, String chunkType, String content) { ... }
public void flushAssistantRoundOnToolCall(String taskId) { ... }
public void finalizeAssistantOnError(String taskId) { ... }
private void persistAssistantRound(String cid, String taskId, AssistantRound round, String status) { ... }
public void upsertToolCallRow(...) { ... }
```

- [ ] **Step 3: 简化 finishAssistant**

```java
/**
 * 任务完成：只推 SSE done 事件，不存消息。
 * 消息已由 Agent 的 MessageStore 按轮写入。
 */
@Transactional
public void finishAssistant(String conversationId, String taskId, boolean ok,
                            String conclusion, String reasoning, String error) {
    if (conversationId == null || conversationId.isBlank()) {
        streamManager.unbindTask(taskId);
        return;
    }
    // 只推 done 事件，不存消息
    Map<String, Object> done = new LinkedHashMap<>();
    done.put("taskId", taskId);
    done.put("status", ok ? STATUS_COMPLETED : STATUS_FAILED);
    done.put("content", conclusion != null && !conclusion.isBlank() ? conclusion : (error != null ? error : "（空回复）"));
    streamManager.push(conversationId, "done", done);
    streamManager.unbindTask(taskId);
    log.info("conversation done pushed: conversation={}, task={}, ok={}",
            conversationId, taskId, ok);
}
```

- [ ] **Step 4: 保留的代码确认**

确保以下方法保留不变：
- `create()`, `list()`, `messages()`, `delete()` — 会话 CRUD
- `send()` — 用户发消息（落 USER 消息 + 发 TaskDispatch）
- `saveApprovalDecision()` — 审批消息写入
- `savePlanUpdateMessage()` — plan 更新消息
- `buildHistory()` — 历史消息组装（查询方式不变）

- [ ] **Step 5: Commit**

```bash
git add ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentConversationService.java
git commit -m "refactor(admin): remove streaming persistence from AgentConversationService"
```

---

### Task 5: Java 端清理 - AgentGrpcService

**Files:**
- Modify: `ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentGrpcService.java`

- [ ] **Step 1: 简化 handleEvent**

```java
private void handleEvent(TaskEvent event) {
    touch();
    String type = event.getEventType();
    String taskId = event.getTaskId();
    switch (type) {
        case "plan_update" -> {
            forwardStreamEvent(event);
            handlePlanUpdate(event.getContent());
        }
        case "plan_advance" -> handlePlanAdvance(taskId, event.getContent());
        default -> forwardStreamEvent(event);  // 纯透传：thinking/delta/tool_call/tool_result/suggestion_created/error
    }
}
```

- [ ] **Step 2: 移除 persistToolMessage 方法**

```java
// 删除整个方法
private void persistToolMessage(String type, TaskEvent event) { ... }
```

- [ ] **Step 3: 简化 handleResult**

```java
private void handleResult(TaskResult result) {
    touch();
    String conversationId = streamManager.conversationOf(result.getTaskId());
    if (conversationId != null) {
        conversationService.finishAssistant(conversationId, result.getTaskId(),
                result.getOk(), result.getConclusion(), result.getReasoning(), result.getError());
    }
    // 刷新审批行（execute 任务）
    try {
        suggestionService.refreshApprovalAfterExecuteTask(result.getTaskId());
    } catch (Exception e) {
        log.debug("refreshApproval skipped (no-op for non-execute tasks): {}", e.getMessage());
    }
}
```

注意：`handleResult` 本身变化不大，因为 `finishAssistant` 已在上一步简化。只需要确认不再调用 `persistToolMessage` 或其他已移除的方法。

- [ ] **Step 4: Commit**

```bash
git add ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentGrpcService.java
git commit -m "refactor(admin): simplify AgentGrpcService to pure SSE pass-through"
```

---

### Task 6: Java 端清理 - Repository 清理

**Files:**
- Modify: `ops-agent-admin/src/main/java/com/opsagent/admin/repository/ConversationMessageRepository.java`

- [ ] **Step 1: 移除不再使用的查询方法**

```java
// 移除以下方法（Agent 端按轮写入，不再需要按 messageId 定位流内行）
Optional<ConversationMessage> findFirstByMessageId(String messageId);  // 移除

// 移除以下方法（Agent 端写入 TOOL_CALL/TOOL_RESULT，不再需要 Admin 端 upsert 锚点）
// 删除 findFirstByToolCallId 方法
```

保留的方法：
- `findByConversationIdOrderByIdAsc()` — 历史查询
- `findByConversationIdAndStatusInOrderByIdDesc()` — 历史组装
- `findFirstByTaskId()` — 兼容旧消息查询
- `findFirstByPayloadSuggestionId()` — APPROVAL 行 upsert 锚点
- `deleteByConversationId()` — 删除会话

- [ ] **Step 2: Commit**

```bash
git add ops-agent-admin/src/main/java/com/opsagent/admin/repository/ConversationMessageRepository.java
git commit -m "refactor(admin): remove unused repository queries for streaming persistence"
```

---

### Task 7: 端到端验证

- [ ] **Step 1: 验证 Agent 端测试通过**

```bash
cd /workspace/ops-agent-core && python -m pytest app/tests/ -v
```

Expected: 所有测试通过（包括现有 graph 测试和新增的 MessageStore 测试）

- [ ] **Step 2: 验证 Java 编译通过**

```bash
cd /workspace/ops-agent-admin && mvn compile -q
```

Expected: BUILD SUCCESS

- [ ] **Step 3: 验证完整数据流**

1. 启动 Agent 和 Admin
2. 发送用户消息
3. 验证 USER 消息由 Admin JPA 写入 `conversation_messages`
4. 验证 Agent 每轮循环后写入 ASSISTANT/TOOL_CALL/TOOL_RESULT 行
5. 验证审批消息由 Admin 写入 APPROVAL 行
6. 验证历史查询返回完整、正确的消息列表

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: migrate message storage from Java streaming to LangGraph per-round persistence"
```