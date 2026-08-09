# Agent 模块重构设计 v3（plans ⇄ suggestions ⇄ tasks + execute 回喂 LLM + 前端流式）

> 2026-08-09 · 用户拍板：数据库表已全部删除，完整重构。
> v3 相对 v2 的调整（用户 16:21 意见）：
> ① suggestions **拆分独立表**（不并入 tasks），与 plan/task 做关联；
> ② execute **执行结果回喂 LLM**：原始工具结果与 LLM 返回都展示，成功/失败 agent 可进一步决策；
> ③ 异步任务保留 **worker 轮询**（Monitor）；
> ④ plan 支持 **agent 主动修改/废弃**；
> ⑤ 系统提示词优化。

## 1. 目标模型（一句话）

- **plans**：一次规划 = 一行（意图：训练并部署），agent 可主动修改/废弃
- **suggestions**：待审批写操作建议 = 一行（plan 的"步骤"角色 + 审批对象），plan_id/step_no 关联
- **agent_tasks**：执行记录 = 一行（chat 对话轮 / execute 执行轮），plan_id/suggestion_id 关联
- 关联：`plan 1—N suggestion 1—0..1 task(execute)`，`chat task 1—N suggestion`（规划产出）
- 三张业务表 + conversation 系；**无 steps JSON**

## 2. 目标架构

```
┌──────────────┐  REST / SSE   ┌──────────────────────┐  gRPC bidi(纯透传)  ┌─────────────────────────────┐
│  前端 (Vue)   │ ────────────► │ admin (Spring Boot)  │ ──────────────────► │ worker (Python / LangGraph) │
│  AgentAssistant│ ◄─────────── │ 对话·消息·SSE·审批动作 │ ◄────────────────── │ 决策循环·工具·规划·轮询·决策  │
└──────────────┘               │ + 只读查询            │                    └─────────────┬───────────────┘
                               └──────────┬───────────┘                                  │ asyncpg 直连（内网）
                                          │ grantKey(Redis)                              ▼
                     ┌────────────────────────────────────────────────────────────────────────────┐
                     │                  PostgreSQL（共享库，DDL 归 admin JPA，表已删待重建）            │
                     │  plans / suggestions / agent_tasks（worker 写业务；admin 只写审批动作）        │
                     │  conversations / conversation_messages（admin 写）                           │
                     └────────────────────────────────────────────────────────────────────────────┘
```

## 3. 数据模型（新）

```sql
-- ===== agent_plans：一次规划（意图，agent 可修改/废弃）=====  写方：worker
CREATE TABLE agent_plans (
  id              BIGSERIAL PRIMARY KEY,
  plan_id         VARCHAR(64)  NOT NULL UNIQUE,
  conversation_id VARCHAR(64)  NOT NULL,
  summary         VARCHAR(255),
  status          VARCHAR(16)  NOT NULL DEFAULT 'PLANNED', -- PLANNED/RUNNING/DONE/FAILED/CANCELLED
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_plans_conversation ON agent_plans(conversation_id);

-- ===== agent_suggestions：待审批写操作建议（plan 的步骤 + 审批对象）=====
-- 写方：worker 写业务（创建 PENDING、执行结果 EXECUTED/FAILED+CANCELLED + result）；
--       admin 写审批动作（APPROVED/REJECTED/EXPIRED + grant_key/confirmed_*）
CREATE TABLE agent_suggestions (
  id              BIGSERIAL PRIMARY KEY,
  suggestion_id   VARCHAR(64)  NOT NULL UNIQUE,
  plan_id         VARCHAR(64),                 -- FK agent_plans；null=非规划的单条建议
  step_no         INT,                         -- plan 内步骤顺序（1..N）
  source_task_id  VARCHAR(64),                 -- 来源任务（chat 轮 / 触发决策的 execute 轮）
  conversation_id VARCHAR(64)  NOT NULL,
  action_type     VARCHAR(32)  NOT NULL,       -- training_create/serving_deploy/training_delete/...
  target_type     VARCHAR(32),
  target_id       BIGINT,
  params          TEXT,                        -- JSON（动作参数）
  reason          TEXT,
  priority        VARCHAR(8)   DEFAULT 'NORMAL',
  status          VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
                  -- PENDING/APPROVED/REJECTED/EXECUTING/EXECUTED/FAILED/EXPIRED/CANCELLED
  grant_key       VARCHAR(128),
  confirmed_by    BIGINT,
  confirmed_at    TIMESTAMPTZ,
  executed_at     TIMESTAMPTZ,
  result          TEXT,                        -- 执行结果（LLM 总结，前端展示；原始结果经 tool_result 事件展示）
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_sug_conversation ON agent_suggestions(conversation_id);
CREATE INDEX idx_agent_sug_plan         ON agent_suggestions(plan_id);
CREATE INDEX idx_agent_sug_status       ON agent_suggestions(status);

-- ===== agent_tasks：执行记录（chat 轮 / execute 轮）=====  写方：worker
CREATE TABLE agent_tasks (
  id              BIGSERIAL PRIMARY KEY,
  task_id         VARCHAR(64)  NOT NULL UNIQUE,
  task_type       VARCHAR(24)  NOT NULL,       -- chat（对话轮）| execute（执行已审批建议）
  plan_id         VARCHAR(64),                 -- 该任务所属 plan（execute 有；chat 可为空）
  suggestion_id   VARCHAR(64),                 -- execute 对应建议
  conversation_id VARCHAR(64),
  query           TEXT,                        -- chat：用户问题；execute：可为空
  status          VARCHAR(16)  NOT NULL DEFAULT 'DISPATCHED', -- DISPATCHED/RUNNING/SUCCEEDED/FAILED/CANCELLED
  worker_id       VARCHAR(64),
  conclusion      TEXT,                        -- 最终答复 / execute 的 LLM 总结
  reasoning       TEXT,                        -- LLM 推理链（chat 轮）
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_tasks_conversation ON agent_tasks(conversation_id);
CREATE INDEX idx_agent_tasks_status        ON agent_tasks(status);
CREATE INDEX idx_agent_tasks_plan          ON agent_tasks(plan_id);
CREATE INDEX idx_agent_tasks_suggestion    ON agent_tasks(suggestion_id);

-- conversations / conversation_messages 改为 agent_conversations / agent_conversation_messages（agent 域统一前缀）
-- 删除：agent_events / task_plans / agent_suggestions(旧表，已删，按本新结构重建)
```

**状态机**

```
plans:      PLANNED → RUNNING → DONE / FAILED / CANCELLED（agent 可随时修改/废弃）
suggestions: PENDING →(approve)→ APPROVED →(execute 任务下发)→ EXECUTING → EXECUTED / FAILED
                    →(reject)→ REJECTED  →(grant 过期)→ EXPIRED  →(用户/agent 取消)→ CANCELLED
agent_tasks chat:    DISPATCHED → RUNNING → SUCCEEDED / FAILED / CANCELLED
agent_tasks execute: DISPATCHED → RUNNING → SUCCEEDED / FAILED / CANCELLED
```

## 4. 职责边界

### worker（智能体主逻辑，业务全写库）
- **chat 任务**：收 TaskDispatch → INSERT agent_tasks(chat, RUNNING) → LangGraph 决策 → 事件 → 收敛
  - 收敛时产写操作：**多步** → INSERT agent_plans(RUNNING) + N 条 agent_suggestions(PENDING, plan_id, step_no)；
    **单条** → INSERT 1 条 agent_suggestion(PENDING, plan_id=null)
  - TaskResult(conclusion/reasoning) → gRPC → admin 落对话消息
- **execute 任务**（含 LLM 回喂）：
  1. 收 TaskDispatch（suggestion_id + action_type/target/params/grant_key）→ INSERT agent_tasks(execute, RUNNING)
  2. **直调写工具**（http_client 注入 grant_key，原始结果）→ 发 tool_call/tool_result 事件（前端展示原始结果）
  3. **回喂 LLM**：原始结果 + 任务上下文 → 单轮 LLM 总结（成功/失败 + 是否需要进一步行动）→ conclusion
  4. 更新 suggestion：EXECUTED/FAILED + result(LLM 总结) + executed_at（条件：status=APPROVED/EXECUTING）
  5. 异步写操作（training_create/serving_deploy）→ 注册 Monitor 轮询对象状态
  6. TaskResult(conclusion) → admin 落对话消息（LLM 总结可见）
- **异步轮询 + 再决策**（Monitor）：
  - 对象达成 SUCCEEDED → 更新 suggestion EXECUTED（若未回写）→ 推进 plan 下一步
  - 对象 FAILED/CANCELLED → **触发决策**：LLM 单轮（对象状态 + plan 上下文）→ 决策动作：
    重试（新 suggestion） / 修改 plan（增删步） / 废弃 plan（CANCELLED+说明） / 汇报
  - 决策结果：直写库 + 经 gRPC TaskEvent(type=plan_update) 上报 → admin 落一条 assistant 消息 + SSE 通知前端刷新 plan 卡片
- **plan 主动修改/废弃**：agent 依据执行结果随时 UPDATE agent_plans（status/summary）与 suggestions（增/删/改）——直写库；并通过 plan_update 事件告知前端
- **超时**：自建扫描协程（chat/execute RUNNING 超时 → 取消 CANCELLED + 关联 suggestion 置 CANCELLED）
- **恢复**：启动扫描 agent_plans(PLANNED/RUNNING) + 关联 suggestions/execute tasks → 按对象状态重建 Monitor / 推进 / 废弃

### admin（对话通信 + 审批动作 + 只读查询）
- conversation CRUD / messages / SSE（不变）
- send()：落 user msg + task_id → TaskDispatch(chat, query, history, convId, token) → push（无 worker 落 failed 消息）
- approve(suggestionId)：`UPDATE agent_suggestions SET status=APPROVED, grant_key=?, confirmed_by=?, confirmed_at=? WHERE id=? AND status='PENDING'`
  → grantKey(Redis) → 生成 execute task_id → TaskDispatch(execute, suggestion_id, action_type/target/params/grant_key, convId, 长TTL token) → push
- reject(suggestionId)：`UPDATE ... SET status=REJECTED WHERE status='PENDING'`
- expireScan：APPROVED/EXECUTING 且 grant 不在 Redis 且 execute task 不在跑 → EXPIRED
- 只读查询：tasks / suggestions / plans 列表
- **删除**：AgentTaskService 状态机（complete/recordEvent/timeoutScan/persistSuggestions 等）、AgentEvent、TaskPlanService 写、gRPC 业务逻辑（handleEvent 只转发 SSE+plan_update 落消息；handleResult 只落对话消息）
- grantKey 校验/消费：写端点 GrantCheckAspect（Redis GETDEL，agent 无法自授权/重放）

### 前端
- 审批卡数据源：`GET /api/agent/suggestions?status=PENDING`（独立表恢复，语义同现状）
- 消息展示：LLM 总结（conclusion/markdown）+ 可折叠原始结果（tool_result 时间线，execute 轮也有）
- plan 卡片：展示 plans + 关联 suggestions 进度（step_no 排序），plan_update 事件到达时刷新
- 流式优化（§7 不变）；stop → cancel API

## 5. proto 变更

```proto
// TaskDispatch：
string task_type = 9;        // chat | execute
string action_type = 10;     // execute：写工具名
int64  target_id = 8;        // 已有
string target_type = 7;      // 已有
string params = 11;          // execute：动作参数 JSON
string grant_key = 12;       // execute：approve 后签发（替代 AuthorizationGrant 单独推送）
string suggestion_id = 13;   // execute：对应建议（worker 回写 suggestion 状态用）

// TaskEvent：新增事件类型 plan_update（content=JSON {planId,status,summary,message}）
// 删除消息：AuthorizationGrant / AsyncSuggestion / TaskPlan / PlanStep
// TaskResult：保留 conclusion/reasoning/error；suggestions 标记 deprecated 不复用
```

Python `gen.sh` 重生成 stubs；Java protobuf-maven-plugin 构建时生成。

## 6. 关键流程

### 6.1 对话轮（chat）+ 规划
```
前端 POST /conversations/{id}/messages
  → admin: 落 user msg + task_id → TaskDispatch(chat, query, history, convId, token) → push
  → worker: INSERT agent_tasks(chat, RUNNING) → LangGraph 决策
  → worker: 事件 → gRPC → admin 仅转发 SSE
  → worker: 收敛 → 多步: INSERT agent_plans(RUNNING) + N 条 agent_suggestions(PENDING, step_no)
                        单条: INSERT 1 条 agent_suggestion(PENDING)
  → worker: TaskResult → admin 落 assistant msg + SSE done → 前端刷新审批卡/plan 卡片
```

### 6.2 审批 + 执行（execute，直调 + LLM 回喂）
```
前端审批卡 approve(suggestionId)
  → admin: 条件更新 APPROVED + grant 落库/Redis → 生成 execute task_id
  → admin: TaskDispatch(execute, suggestion_id, action_type, target, params, grant_key, convId, 长TTL token) → push
  → worker: INSERT agent_tasks(execute, RUNNING) + UPDATE suggestion(EXECUTING)
  → worker: registry.get(action_type) 直调写工具（带 grant_key）→ tool_call/tool_result 事件（原始结果展示）
  → worker: 原始结果回喂 LLM → 单轮总结（成功/失败/后续建议）→ conclusion
  → worker: UPDATE suggestion(EXECUTED|FAILED, result=LLM 总结, executed_at) 条件 status IN (APPROVED,EXECUTING)
  → worker: 异步写操作 → 注册 Monitor 轮询对象状态
  → worker: TaskResult(conclusion) → admin 落 assistant msg（LLM 总结）+ SSE done
```

### 6.3 异步轮询 + 再决策 + plan 修改/废弃
```
Monitor 轮询对象状态
  ├─ SUCCEEDED → 更新 suggestion EXECUTED（若未回写）→ 推进 plan 下一步（新 suggestion 前端可见）→ 全 done → agent_plans(DONE)
  └─ FAILED/CANCELLED → LLM 决策（对象状态+plan 上下文）
        ├─ 重试 → INSERT 新 suggestion(PENDING)
        ├─ 修改 plan → UPDATE agent_plans/agent_suggestions（增删改步骤）
        ├─ 废弃 plan → UPDATE agent_plans(CANCELLED) + 说明
        └─ 汇报 → 生成总结
      决策结果直写库 → TaskEvent(plan_update) → admin 落 assistant msg + SSE 刷新
```

### 6.4 取消
```
前端 stop → POST /conversations/{id}/tasks/{taskId}/cancel
  → admin: CancelTask gRPC → worker 取消 → task CANCELLED（关联 suggestion 置 CANCELLED）
  → admin: 落 failed 消息 + SSE error
超时（worker 自治）：扫描 RUNNING 超时 → 同上
```

### 6.5 恢复
```
worker 启动 → SELECT * FROM agent_plans WHERE status IN (PLANNED,RUNNING)
  → 每步 suggestion（PENDING/APPROVED/EXECUTING）：
      对象进行中 → 重建 Monitor 轮询；已达成 → 推进；失败 → 决策/废弃
```

## 7. 系统提示词优化（SYSTEM_PROMPT / 工具提示）

1. **规划输出契约**（替换原 plan JSON 块）：多步写操作时输出 `plan` 摘要 + 每条写操作一条 `suggestion`
   （不再输出 steps 数组；步骤 = 多条 suggestion，由系统按顺序落 step_no）
2. **execute 语义**：你正在执行已获人工审批的写操作——调用工具后**必须依据原始结果总结**：
   成功/失败、影响、后续建议；原始结果与你的总结都会展示给用户，禁止编造执行结果
3. **plan 自主权**：执行或轮询中发现条件变化、失败、需求不成立时，可主动修改 plan（增删步骤）、
   废弃 plan（说明理由）或提出替代建议——所有变更须通过 plan_update 事件向用户说明
4. **建议格式**：每个写操作一条 suggestion（action_type/target/params/reason/priority），
   信息不足时一次性询问缺失参数
5. 保留：工具契约注入、禁止编造、审批闭环红线、多轮上下文纪律（不重复调工具）

## 8. 错误处理与一致性

| 场景 | 策略 |
|---|---|
| worker 写库失败 | task/suggestion 记 FAILED，TaskResult 带 error，admin 落 failed 消息 |
| approve 并发 | 条件更新 `WHERE status='PENDING'`，第二次不生效 |
| expire 与执行并发 | worker 回写要求 `WHERE status IN (APPROVED,EXECUTING)`；expireScan 跳过运行中 |
| LLM 回喂失败 | execute 用原始结果结构化总结兜底（不阻塞任务） |
| worker 崩溃 | task 停留 RUNNING → 重启恢复（§6.5）或超时协程 CANCELLED |
| grant 消费 | 写端点 GrantCheckAspect 原子 GETDEL（不变），无法重放 |

## 9. 测试策略

- Python：test_plan（规划落库 plans+suggestions）、test_execute（直调+LLM 回喂+状态回写）、
  test_tracker（轮询/推进/再决策/plan 废弃）、test_recovery、事件聚合、提示词契约解析
- E2E：chat 流式事件序；规划→审批→execute→EXECUTED（原始结果+LLM 总结展示）；
  多步 plan 全链路；失败→再决策→plan 废弃；worker 直写库断言；恢复；取消链路
- 前端：审批卡（suggestions 独立表）、plan 卡片 + plan_update 刷新、流式节流效果

## 10. 分步实施

| 阶段 | 内容 | 验证 |
|---|---|---|
| P0 | 修 tracker 漏 await + 补 test_tracker.py（旧模型止血） | pytest 绿 |
| P1 | worker DB 层：asyncpg + plan/suggestion/task repo + 事件聚合 + 超时 + 恢复 | worker 直写库 |
| P2 | proto 重构（TaskDispatch 增字段、TaskEvent 加 plan_update、删 4 消息）+ 重生成 | 编译通过 |
| P3 | admin：实体重构（Plan/Suggestion/AgentTask 新模型，删旧实体）+ 瘦身 gRPC + approve/reject/cancel API + expireScan | E2E 全链路 |
| P4 | 前端：流式优化 + plan 卡片 + 审批卡改 suggestions 独立表 + stop→cancel | 手工验收 |
| P5 | 提示词优化落地 + E2E 补强 + 部署验证（JPA 重建新表） | 全绿 |

## 11. 迁移与部署

- 表已删 → admin 启动 JPA ddl-auto 重建 plans/suggestions/agent_tasks；cleanup SQL 更新
- worker requirements 加 asyncpg；compose agent 加 PG env（DATABASE_URL，PG 不映射宿主）
- **风险**：worker 直连库需 PG 凭据下发（compose env，内网可达）
