# Agent Plan 终态自然语言总结设计

> 日期：2026-08-09
> 状态：设计已确认；**实际落地范围已缩小**

## 0. 范围变更（2026-08-09 收尾）

用户评估后决定不做 DB/后端/前端/SSE 那套结构化改造（认为太复杂），**仅改动系统提示词**让 agent 的
最终总结输出为 Markdown 消息（前端已支持 Markdown 渲染）。因此本文第 3–7 节（落库、admin 透传、前端渲染、
测试）**暂不实现**，仅第 2 节"触发点"与"输出格式"相关的提示词约束生效。

实际改动：
- `ops-agent-core/app/agent/core.py` 的 `SYSTEM_PROMPT`：
  - 第 8 步明确"任务收尾的最终总结必须是一段 Markdown 消息（用 `##` 标题分段、列表或表格呈现）"。
  - 输出格式段新增"**最终总结**"条目：强制最终总结为 Markdown 格式消息，避免纯文本长段落。

生效方式：重新部署 agent worker（提示词在 worker 启动时载入），无需改库/改前端。

## 1. 背景与目标

Agent 在执行任务时会通过 `plan_create` 建立结构化计划（`summary` + `steps[]`），并在执行过程中用
`plan_update` 把计划或其步骤置为终态（`DONE` / `FAILED` / `CANCELLED`）。当前计划走到终态时，前端只收到
`plan_update` 事件，展示的是简短的状态行（"计划已完成：<summary>"），缺少一段**面向人的自然语言复盘**。

目标：当 plan 走到任意终态时，由 worker 用 LLM 生成一段 1–3 句的中文总结（做了什么、结果如何、有无失败及原因），
以**独立 plan 卡片小结**的形式推给前端，并持久化以便历史会话重开时直接展示。

已确认的三项决策：
- 总结由 **LLM 生成自然语言**（非结构化统计）。
- 展示位置为 **独立 plan 卡片小结**（不并入最终答复消息）。
- **DONE / FAILED / CANCELLED 三种终态都触发**。

## 2. 触发点（唯一收敛点）

`plan_update` 的 plan 级终态分支是 plan 走到 DONE/FAILED/CANCELLED 的唯一收敛点：

- `ops-agent-core/app/agent/graph.py:481` `handle_plan_update`
  - 步骤级（`step_no > 0`）在 `graph.py:501-514` 提前返回，**不**触发总结。
  - plan 级终态在 `graph.py:516-526`：校验 `plan_status ∈ {DONE,FAILED,CANCELLED}` → `store.update_plan_status` → `notify`。
  - 在该分支末尾、notify 之后，调用总结生成逻辑。
- 调用方为 `tools_node`（`graph.py:763-766`），处于 `build_graph` 闭包内，可直接拿到 `llm_runtime`。

## 3. Worker 侧实现

### 3.1 入口改造

`tools_node` 派发 `plan_update` 时，额外传入 `llm_runtime`：

```python
result = await handle_plan_update(store, ctx, args, notify=tracker_notify, llm_runtime=llm_runtime) \
    if store is not None else {...}
```

`handle_plan_update` 签名新增 `llm_runtime: Any = None`。在 plan 级终态分支（`graph.py:518-526` 之后）追加：

1. **幂等保护**：先 `await store.get_plan(plan_id)`，若 `status` 已是终态，跳过总结生成（避免重复 DONE 触发双写）。
2. 调用 `generate_plan_summary(...)`。
3. 无论成功失败都 `notify`（保持现有 plan_update 行为不变）。

### 3.2 总结生成函数（新增，放在 graph.py 或独立模块）

`generate_plan_summary(llm_runtime, store, ctx, plan_id, plan_status)`：

- `plan = await store.get_plan(plan_id)` → 含 `summary`、`steps`(List[dict])、`conversation_id`。
- 构造紧凑 prompt：
  - system：你是运维智能体的总结器，给定计划目标与步骤结果，用 1–3 句简体中文说明做了什么、结果如何、有无失败及原因；不用 markdown、不编号；全成功则语气肯定，有失败则点明失败点。
  - user：计划目标 = `{plan.summary}`；步骤结果 = 逐行 `{step_no}. {action_type} → {status} {note}`。
- 调用 **fast 实例**（`llm_runtime.select(False)`，关闭思考）以控延迟与成本，设短超时（约 15s）。
- 解析出一段 `summary_text`（截断到 ~200 字）。
- 持久化：`await store.update_plan_summary(plan_id, summary_text)`。
- 推事件：`await client.send_event(ctx.task_id, "plan_summary", json.dumps({
    "planId": plan_id, "conversationId": plan["conversation_id"],
    "status": plan_status, "summary": summary_text}, ensure_ascii=False))`。

### 3.3 失败兜底

LLM 调用异常 / 超时 → 不抛、不阻塞，回退结构化文案：

```
计划已{status_zh}，共 {n} 步：{done} 完成 / {failed} 失败 / {cancelled} 取消
```

保证卡片永远有内容；兜底文案同样走 3.2 的持久化 + 事件推送。

## 4. 落库（task_store.py）

`ops-agent-core/app/agent/task_store.py`：

- `agent_plans` 新增列 `summary_text TEXT`（nullable）。提供 migration：
  `ALTER TABLE agent_plans ADD COLUMN IF NOT EXISTS summary_text TEXT;`
  （放 `scripts/` 下的迁移 SQL，并在 deploy/init 流程执行；已存在的表靠 `IF NOT EXISTS` 安全幂等）。
- 新增方法：

```python
async def update_plan_summary(self, plan_id: str, summary_text: str) -> None:
    await self.db.execute(
        "UPDATE agent_plans SET summary_text=$2, updated_at=now() WHERE plan_id=$1",
        plan_id, summary_text)
```

- `upsert_plan`（`task_store.py:69`）不写 `summary_text`（创建时留空），避免覆盖已有总结。
- `get_plan`（`task_store.py:107`）为 `SELECT *`，自动带出 `summary_text`，历史重载无需改。

## 5. Admin 侧透传（必改，否则事件被吞）

`ops-agent-admin/.../service/agent/AgentGrpcService.java`：

- `handleEvent`（`AgentGrpcService.java:84`）：新增分支

```java
if ("plan_summary".equals(type)) {
    handlePlanSummary(event.getContent());
    return;
}
```

- 新增 `handlePlanSummary(String content)`：解析 `{planId, conversationId, status, summary}`，校验
  `conversationId` 非空后调用
  `streamManager.push(conversationId, "plan_summary", Map.of("planId", planId, "status", status, "summary", summary))`。
  （不落 chat 消息——与"独立卡片小结"定位一致；DB 行已由 worker 直写。）
- `agent.proto:66/71` 注释补充 `plan_summary` 说明（仅文档性，proto 字段为自由字符串无需改结构）。

## 6. 前端渲染

- `ops-agent-front/.../stores/agent.js`：在 SSE 分发中新增 `case 'plan_summary'`——按 `planId` 定位当前消息里的
  plan 对象（plan_update 事件已建立/更新该 plan 条目），写入 `plan.summary = payload.summary`。
- `AgentAssistant.vue`：在 plan 卡片底部渲染小结区——状态角标 + `plan.summary` 文本；`FAILED` 用警示色。
- 历史重载：会话加载时 plan 来自后端 `summary_text`，映射到 `plan.summary` 即可直接展示，无需重算。

## 7. 测试

- 单元（worker）：`test_internal_tools.py` 扩展——构造 DONE/FAILED/CANCELLED 三种 plan，断言
  `plan_summary` 事件 payload 含 `summary` 与 `conversationId`，且 `store.update_plan_summary` 被调用。
- 兜底：mock LLM 抛异常，断言回退结构化文案、不抛错、事件仍发出。
- 幂等：连续两次 `plan_update(DONE)`，第二次不重复生成（get_plan 状态已终态跳过）。
- 集成/E2E：跑一次含多步 approve_* 的真实 plan，验证前端卡片小结出现、历史重开可见。
- admin：单测 `handlePlanSummary` 正确路由到 `streamManager.push` 且缺 conversationId 时静默忽略。

## 8. 改动清单（一览）

| 模块 | 文件 | 改动 |
|------|------|------|
| core | `app/agent/graph.py` | `handle_plan_update` 加 `llm_runtime` 形参 + 终态调 `generate_plan_summary`；新增生成函数 + 兜底 |
| core | `app/agent/task_store.py` | `update_plan_summary` 方法；`agent_plans.summary_text` 列 |
| core | `scripts/*.sql` + deploy | `ALTER TABLE ... ADD COLUMN summary_text` 迁移 |
| admin | `AgentGrpcService.java` | `handleEvent` 加 `plan_summary` 分支 + `handlePlanSummary` |
| admin | `agent.proto` | 注释补充（无结构改动） |
| front | `stores/agent.js` | `case 'plan_summary'` 写入 plan.summary |
| front | `AgentAssistant.vue` | plan 卡片底部渲染小结 |
