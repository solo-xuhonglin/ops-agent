# Agent 执行循环重构 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 去掉 continue 逻辑，agent 内部闭环（思考→工具→…→回答），异步任务用 wait_until 长查询工具自主轮询；全系统统一 LangGraph 决策路径。

**Architecture:** 任务收敛为 chat/execute 两类（删 continue）；execute 改任务内闭环（系统直调写工具→同一任务决策图→wait_until 轮询→收敛）；Monitor 机械判终态后触发推进轮（worker 自治，复用决策图）；删除 decision.py 旁路循环。设计依据：`docs/plans/2026-08-09-agent-loop-refactor-design.md`。

**Tech Stack:** Python 3.13（ops-agent-core，LangGraph 1.2.10 / langchain-deepseek / httpx / pytest 8.3.3）、Java 21 + Spring Boot（ops-agent-admin）、gRPC 双向流。本地 pytest 可跑（managed venv：`C:/Users/wangc/.workbuddy/binaries/python/envs/default/Scripts/python.exe`，基线 65 passed）；**admin 不本地编译**（走远端 deploy.sh）。

**Test commands:**
- 全量：`cd ops-agent-core && C:/Users/wangc/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pytest tests/ -q`
- 单测：`... -m pytest tests/test_xxx.py -q`

---

### Task 1: wait_until 内置工具（worker）

**Files:**
- Modify: `ops-agent-core/app/agent/graph.py`
- Test: `ops-agent-core/tests/test_wait_until.py`（新建）

**Step 1: 写失败测试**（test_wait_until.py，mock http/registry，测四个行为）
- 提前返回①：查询 status 命中 target_status → 立即返回（只查 1 次）
- 提前返回②：查询返回终态 FAILED → 立即返回（即使不是 target）
- 提前返回③：updated_at 变化 → 返回最新数据
- 超时返回：wait_seconds 内无变化 → 返回 `still_in_progress: true`
- 返回体含提取的 status/updated_at
- 等待期间发了 progress 事件
- 查询工具缺失 → 直接返回错误，不循环

**Step 2: 跑测试确认失败**（`pytest tests/test_wait_until.py -q` → import/handler 不存在 FAIL）

**Step 3: 实现**（graph.py）
- `BUILTIN_TOOL_SCHEMAS["wait_until"]`：OpenAI schema（query_tool enum、jobId/endpointId/datasetId、wait_seconds 1-120 默认 60、target_status）
- `async def handle_wait_until(registry, http, client, ctx, args)`：
  - 取 `args["query_tool"]` → `registry.get`，缺失返回 `{status:0, body:"unknown query_tool"}`
  - 提取对象 id 参数（jobId/endpointId/datasetId）
  - `deadline = loop.time() + wait_seconds`；循环（间隔 3-5s）：
    - `result = await http.call(tool, {id_param: id}, ctx)`
    - 解析 body → `status` / `updated_at`（容错：updatedAt/updatedTime/缺失则退化，只比较 status）
    - 命中 target_status / 终态集合（FAILED/CANCELLED/STOPPED）/ updated_at 变化 → 立即返回
    - 超时 → 返回当前最新结果 + `still_in_progress: true`
  - 每次查询前发 `progress` 事件（「等待 {query_tool} 完成，当前状态 {status}」）
- `tools_node` 分发加分支：`name == "wait_until"` → `handle_wait_until(registry, http, client, ctx, args)`

**Step 4: 跑测试确认通过**

**Step 5: Commit** — `git commit -m "feat(core): add wait_until builtin tool with timeout and early-return"`

---

### Task 2: execute 任务内闭环（worker）

**Files:**
- Modify: `ops-agent-core/app/agent/core.py`
- Test: `ops-agent-core/tests/test_execute_loop.py`（新建）

**Step 1: 写失败测试**（test_execute_loop.py）
- 写工具成功（mock http 返回 200 + body 含 id）→ handle_execute 内部构建决策图 → agent 推进（mock llm 返回工具调用/收敛）→ send_result 结论为最终 assistant 内容
- 写工具失败 → 图内 agent 看失败（mock llm 收敛文本含失败）→ suggestion 置 FAILED、send_result ok=False
- suggestion 状态更新/任务落库仍发生（保留现状行为）
- Monitor 仍注册（写工具成功时）

**Step 2: 跑测试确认失败**

**Step 3: 实现**（core.py）
- 新增 `EXECUTE_LOOP_SYSTEM`（写操作已由系统执行 + 结果如下 + 异步则用 wait_until 确认 + 按 plan 推进 + 非工具则结束）
- 改造 `handle_execute`：
  - 直调写工具（现状保留，含 call_id/事件/suggestion 状态/task 落库/Monitor 注册）
  - 构建消息：`[SystemMessage(SYSTEM_PROMPT + EXECUTE_LOOP_SYSTEM), HumanMessage(写操作结果 JSON + plan 状态摘要)]`
  - `build_graph(...)` + `run_graph(...)`（复用）→ `_extract_conclusion` 作结论
  - 写工具失败：suggestion 置 FAILED 保留，但仍进图（agent 看失败决定重试/放弃），结论 = 图收敛文本（若图失败则回退现状兜底文本）
  - `send_result` / `finish_task` 收尾不变
- `handle_dispatch` 的 execute 分支不变（仍走 handle_execute）

**Step 4: 跑测试确认通过（全量 65+ 不回归）**

**Step 5: Commit** — `git commit -m "feat(core): run execute task as in-task decision loop"`

---

### Task 3: SYSTEM_PROMPT 增补 wait_until 指引（worker）

**Files:**
- Modify: `ops-agent-core/app/agent/core.py`（SYSTEM_PROMPT）

**Step 1:** 增补「wait_until 使用」段落：何时用（异步写操作后确认结果/等待状态变化）、用法（query_tool+对象id+wait_seconds 60~120+target_status）、何时不用（普通查询直接查/已到终态/非异步）、预算意识（连续等待不超数分钟，超时返回 still_in_progress 且预算将尽时汇报「仍在进行中，系统会在完成时继续处理」，勿无限等待）。

**Step 2: Commit** — `git commit -m "feat(core): document wait_until usage timing in system prompt"`

---

### Task 4: tracker 推进轮（worker）

**Files:**
- Modify: `ops-agent-core/app/agent/tracker.py`
- Test: `ops-agent-core/tests/test_tracker_advance.py`（新建）

**Step 1: 写失败测试**（test_tracker_advance.py）
- `_on_done`：suggestion 机械置 EXECUTED（store.update_suggestion_result 被调）→ 触发推进轮（mock llm 收敛）→ 发了 plan_advance 事件（task_id=`plan_advance:{plan_id}`）+ send_result/plan_update
- `_on_failed`：suggestion 置 FAILED → 推进轮
- 无 plan_id 或无 llm：降级为 plan_update 通知（不跑图）

**Step 2: 跑测试确认失败**

**Step 3: 实现**（tracker.py）
- 新增 `_run_advance(monitor, terminal_status, observation)`：
  - task_id = `plan_advance:{monitor.plan_id}`
  - 先 `client.send_event(task_id, "plan_advance", json{conversationId, planId, status, message})`（admin 据此 bindTask）
  - 构建消息：`[SystemMessage(DECISION_SYSTEM 内容迁到此处或复用 core.SYSTEM_PROMPT 派生), HumanMessage(观察 + plan 状态)]` —— 用 `_format_plan` 逻辑
  - `build_graph(...)` + `run_graph(...)` → 结论经 `client.send_result(task_id, True, conclusion)`；store 落 task 行（类型 `advance`）+ finish_task
  - 异常 try/catch 不阻塞（log + plan_update 通知兜底）
- `_on_done`/`_on_failed`：suggestion 机械更新后调用 `_run_advance`；**删除**对 `decision.py run_decision_round` 的调用；保留无 llm 机械降级
- 注意：推进轮用 `self.llm.select(True)`（thinking 决策）

**Step 4: 跑测试确认通过（全量不回归）**

**Step 5: Commit** — `git commit -m "feat(core): add in-graph advance round on monitor terminal state"`

---

### Task 5: 删除旧逻辑（worker）

**Files:**
- Delete: `ops-agent-core/app/agent/decision.py`
- Modify: `ops-agent-core/app/agent/core.py`（删 `handle_continue`、`handle_dispatch` 的 continue 分支、`_build_prompt` 相关引用）
- Modify: `ops-agent-core/app/agent/tracker.py`（删残留 decision import）
- Delete: `ops-agent-core/tests/test_continue.py`
- Modify/Delete: `ops-agent-core/tests/test_decision.py`（其中工具分发逻辑已有等价覆盖则删，保留有价值的挪到 test_graph.py）

**Step 1:** 删除文件与分支；`grep -ri "continue\|run_decision_round\|run_failure_decision\|handle_continue" app/ tests/` 确认无残留（排除 wait_until 循环内的 continue 关键字）

**Step 2: 跑全量测试** — 预期通过（delete 类任务无新增行为，只清理）

**Step 3: Commit** — `git commit -m "refactor(core): remove continue task type and decision.py side loop"`

---

### Task 6: admin 清理 + plan_advance 事件（admin）

**Files:**
- Modify: `ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentGrpcService.java`
- Modify: `ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentConversationService.java`
- Modify: `ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentTaskService.java`

**Step 1: 清理** — `AgentGrpcService.handleResult` 删 autoContinue 调用；`AgentConversationService.autoContinueForPlanStep` 整删；`AgentTaskService.dispatchContinuePlanStep` 整删（含 unused imports）

**Step 2: 新增 plan_advance 事件处理** — `AgentGrpcService` 事件分发加 `handlePlanAdvance(content)`：解析 `{taskId(=plan_advance:{planId}), conversationId, planId, status, message}` → `streamManager.bindTask(taskId, conversationId)` + 落 assistant 消息（「计划推进中：{message}」）+ SSE push；TaskResult 正常路径已能落最终结论

**Step 3:** `grep -rn "autoContinue\|dispatchContinuePlanStep\|plan_advance" src/` 核对

**Step 4: 远端构建验证**（本地不编译）：push → `scripts/ssh_deploy.py admin` → 确认容器起来无编译错误（编译失败会显示在日志）

**Step 5: Commit** — `git commit -m "refactor(admin): remove autoContinue dispatch, handle plan_advance event"`

---

### Task 7: 全链路验证 + 部署（收尾）

**Files:**
- Modify: `ops-agent-test/`（agent E2E，按需更新 continue 相关断言）
- 部署：push → `scripts/ssh_deploy.py admin core` → 容器健康检查

**Step 1:** 更新 E2E：execute 内闭环 + wait_until 路径；确认无 continue 断言残留

**Step 2:** 本地 worker 全量测试绿 + E2E 脚本跑通

**Step 3:** 部署验证：登录前端验证 审批→execute→推进 全链路；`grep -rn continue` 远程确认无残留

**Step 4: Commit** — `git commit -m "test: update e2e for in-task loop and wait_until"`

---

### 注意（项目约定）
- 日志/commit message 英文；docstring/注释中文；前端文案中文
- 部署一律走 GitHub push → 远端 git pull → 构建（禁止 SFTP 覆盖）
- push 后确认 `git status -sb` 无 ahead 再触发部署
