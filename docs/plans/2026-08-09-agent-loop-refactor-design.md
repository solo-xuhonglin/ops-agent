# Agent 执行循环重构：任务内闭环 + wait_until 长查询 + Monitor 决策兜底

> 2026-08-09。背景：解决「agent 不继续执行」而引入的 continue 逻辑不合理 —— agent 应内部闭环
> （思考 → 工具 → 思考 → 工具 → 思考 → 回答，非工具则结束），审批提交后结束/暂停、审批同意由
> admin 唤起，异步任务由 agent 用**长查询工具自主轮询**，不再依赖 admin 派发 continue。

## 一、现状问题

execute 之后存在**两套并行的 plan 推进机制**，职责重叠、重复触发：

1. **admin 侧 autoContinue**：execute 的 TaskResult 到达瞬间 → `autoContinueForPlanStep` →
   `dispatchContinuePlanStep` 派发新 `continue` 任务 → worker `handle_continue` 走
   `decision.py` 独立循环推进 plan。
2. **worker 侧 Monitor 轮询**：写工具成功后注册 Monitor → 后台 10s→300s 指数退避查询 →
   到终态 → `_on_done`/`_on_failed` 又跑一次 `decision.py` 的 `run_decision_round`。

问题：
- continue 派发时机在 execute 结果到达瞬间，**异步对象往往尚未到终态**，模型只会空转
  「仍在进行中」，这就是「agent 不继续执行」的感知来源。
- 两条路径可能重复触发决策，plan 步骤状态被多次改写。
- 决策实现分裂：LangGraph 决策图（chat）与 decision.py 独立循环（推进）两套并存。

## 二、目标架构

- 任务收敛为两类：**chat**（用户消息 → 完整决策循环）、**execute**（审批后 admin 唤起，
  系统直调写工具 → 同一任务内决策图 → 自主轮询推进）。**删除 continue**。
- 新增内置工具 **wait_until**：带超时与提前返回（状态变化 / updated_at 更新即返回）的
  长查询，agent 在任务内自主轮询异步对象状态。
- **全系统只有一条决策路径（LangGraph 决策图）**：chat、execute 内闭环、Monitor 终态后
  的「推进轮」全部复用同一张图 + wait_until + plan_update + approve_*。
- Monitor 保留为**任务外长等待兜底**：机械判定终态（不烧 LLM）→ suggestion 机械置
  EXECUTED/FAILED → 触发推进轮（LLM 决策后续步骤）。
- 不向前兼容：旧 continue 相关逻辑全部清理干净，不保留防御分支。

```
用户消息 / 新对话
  ↓
chat 决策循环（LangGraph）：思考 ↔ 工具循环，非工具则结束
  ├─ 无工具调用 → 回答并结束
  └─ 需要写操作 → approve_* 落建议 → 任务挂起，等待人工审批
                                        ↓ 人工审批通过
                                  admin 唤起 execute
                                        ↓ 系统直调写工具（不进 tools）
                                  任务内推进（同一任务）：wait_until 长查询 → 终态决策
                                        ├─ 又提新建议 → 再等审批（循环）
                                        ├─ plan DONE → 回答结束
                                        └─ wait 预算用尽 → Monitor 兜底（后台盯状态）
                                             → 终态 → 推进轮（决策图）→ 结论落对话
```

## 三、wait_until 长查询工具

**定位**：内置工具（与 plan_create/plan_update 同级，tools_node 本地分发），不进 registry
（registry 是 admin 下发的业务工具，wait_until 是 worker 侧行为封装）。

**OpenAI function schema**：

```json
{
  "name": "wait_until",
  "description": "等待异步对象状态到达目标/发生变化，带超时与提前返回。系统代为循环查询，"
                 "对象状态变化、updated_at 更新、到达 target_status 或进入终态时立即返回最新状态；"
                 "wait_seconds 内无变化则返回当前最新状态（仍在进行中）。提交异步操作后等待完成时使用。",
  "parameters": {
    "properties": {
      "query_tool": { "enum": ["training_get", "serving_get", "dataset_get"], "description": "要等待的只读查询工具" },
      "jobId": { "type": "integer" },
      "endpointId": { "type": "integer" },
      "datasetId": { "type": "integer" },
      "wait_seconds": { "type": "integer", "minimum": 1, "maximum": 120, "default": 60 },
      "target_status": { "type": "string", "description": "期望状态（可选），如 SUCCEEDED；不填则等任意变化" }
    },
    "required": ["query_tool"]
  }
}
```

**执行语义**（worker 侧 `handle_wait_until`）：

1. 按 `query_tool` 从 registry 取查询工具，循环调 `http.call`（间隔 3-5s）。
2. **提前返回**（任一命中立即返回最新查询结果）：
   - `status == target_status`（到达目标）；
   - 进入终态（`FAILED`/`CANCELLED`/`STOPPED` —— 即使不是 target，失败也要让 agent 看到）；
   - `updated_at` 变化（对象有更新 → 返回最新数据，agent 可基于新信息决策）。
3. **超时返回**：`wait_seconds` 用尽 → 返回当前最新状态 + `still_in_progress: true`。
4. 返回体 = 查询结果原文 + 提取的 `status`/`updated_at`，避免模型二次猜测。
5. 等待期间发 `progress` 事件（「等待训练完成，当前状态 RUNNING」），前端时间线不干等。

**约束**：单次上限 120s（httpx 单查询 30s 超时不受影响，等待在 worker 内 sleep）；
模型可连续调用 2-3 次覆盖数分钟；总预算由决策图 `max_rounds` 兜住，用尽即收敛 → 走 Monitor。

## 四、execute 内闭环

现状 execute = 直调写工具 → LLM 总结 → 秒回。改造为：

1. 系统直调写工具（**安全边界保留：写工具仍不进 tools**，grantKey 校验不变）。
2. 结果注入 → **同一任务内构建决策图**：初始消息 = 决策系统提示（含写操作结果观察 + plan
   状态）→ `run_graph`。
3. agent 自主推进：需要等异步对象用 `wait_until`；到终态用 `plan_update` 标记步骤 done、
   提下一步 `approve_*`（带 plan_id/step_no）、或收尾置 plan DONE。
4. **非工具调用则收敛**，最终 assistant 内容即结论（复用 `_extract_conclusion`）。
5. 写操作失败同样在图内：agent 看失败原因 → `approve_*` 带 `retry_of` 修正建议重试，
   或 `plan_update` FAILED/CANCELLED 放弃。
6. 建议 execute 派发时 `reasoning_enabled=true`（内闭环需推理决策，区别于旧 execute 的
   fast 总结模式）。

## 五、Monitor 兜底 + 推进轮

**职责划分（关键）**：
- Monitor（worker 后台机械循环）：**判定终态是确定性的**（status 命中终态集合
  `SUCCEEDED`/`FAILED`/`CANCELLED`/`STOPPED` 或 `target_status`），不烧 LLM；
  到终态后 suggestion 机械置 `EXECUTED`/`FAILED`（幂等）→ 触发**推进轮**。
- 推进轮（LLM 决策后续步骤）：worker **自治发起**（不经 admin dispatch），复用 LangGraph
  决策图 —— 观察数据 + plan 状态作初始消息，agent 用 wait_until/plan_update/approve_*
  决定：步骤 done/failed？重试或放弃？下一步哪个 approve_*？plan 收尾 DONE/FAILED？

**推进轮任务身份与事件回传**：
- task_id = `plan_advance:{plan_id}`（便于排查）。
- 发起时先发 `plan_advance` 事件（content 带 conversationId/planId）→ admin 端
  `bindTask(taskId, conversationId)` + 落 assistant 消息「计划推进中…」；
  后续 thinking/tool_call/tool_result/result 走现有通道（SSE/落库）。

**注册时机**：写工具成功即注册（execute 内闭环与 Monitor 并行，任务收敛后无缝交接；
Monitor 先到终态则推进轮与任务内 wait_until 竞态，靠 suggestion 状态机条件更新幂等收敛）。

## 六、删除清单（不向前兼容）

**worker（Python）**：
- `core.py::handle_continue`：删除（continue 分支不再存在）。
- `decision.py` 整文件删除（`run_decision_round`/`run_failure_decision`/`_run_decision_loop`/
  `_execute_decision_tool`；其工具分发逻辑在 `graph.tools_node` 已有等价实现）。
- `tracker.py`：`_on_done`/`_on_failed` 改为「suggestion 机械更新 + 触发推进轮」，
  不再直接调用 decision.py。
- 防御分支：不保留（收到 continue 类型任务直接按未知类型处理/忽略）。

**admin（Java）**：
- `AgentGrpcService.handleResult`：去掉 execute 成功后 `autoContinueForPlanStep` 调用。
- `AgentConversationService.autoContinueForPlanStep`：删除。
- `AgentTaskService.dispatchContinuePlanStep`：删除。
- 新增 `plan_advance` 事件处理（bindTask + 落 assistant 消息）。
- proto 不动（task_type 是字符串，只是不再派发 continue）。

## 七、提示词优化（让 LLM 知道何时用 wait_until）

`SYSTEM_PROMPT` 增补：

- **何时用**：提交异步写操作（training_create/serving_deploy）后需要确认结果时；
  对象可能仍在进行中、需要等待状态变化时。用法：
  `wait_until(query_tool, jobId|endpointId, wait_seconds=60~120, target_status)`。
- **何时不用**：普通状态查询直接用 training_get/serving_get 等；已确认到达终态不等待；
  非异步操作不等。
- **预算意识**：wait_until 占用任务轮次，连续等待不要超过数分钟；超时返回
  `still_in_progress` 且预算接近用尽时，汇报「仍在进行中，系统会在完成时继续处理」
  （由 Monitor 接管），不要无限等待。
- execute 内闭环决策提示：写操作已由系统执行（结果如下）；如为异步操作请用 wait_until
  确认对象状态，再按 plan 推进。

## 八、边界与错误处理

- **任务取消**：admin CancelTask → `NodeCancelledError` → 现状已处理；推进轮取消同路径。
- **重复推进防护**：Monitor `finished` 标志（现状）；suggestion 状态机条件更新
  （`update_suggestion_result` 幂等）；推进轮以 plan_id 为 task_id，重复触发可被
  admin 侧去重（可选）。
- **轮数上限**：决策图 `recursion_limit` 兜底；推进轮 `max_rounds` 独立可配。
- **SSE 时长**：execute 挂数分钟，keepalive 15s 已覆盖；wait_until 期间 progress 事件。
- **查询失败**：wait_until 内部查询异常按「未变化」处理继续等待，连续失败 N 次提前返回
  错误状态。

## 九、测试计划

- 单测（ops-agent-core/tests）：
  - `wait_until` handler：提前返回（target/终态/updated_at 变化）、超时返回、
    查询失败兜底（mock http）。
  - execute 内闭环：写成功 → agent 推进 → 收敛（mock llm/tools/store）。
  - 推进轮：Monitor 终态 → 决策图发起 → 结论回传。
  - 删除 `test_continue.py`；`test_decision.py` 改造为决策图内工具分发测试。
- E2E（scripts/agent_e2e_runner.py）：审批 → execute → wait_until → 推进 → plan DONE
  全链路；长任务 Monitor 兜底 → 推进轮路径。

## 十、实施步骤（建议顺序）

1. worker：新增 wait_until 工具 + handler + 单测。
2. worker：execute 内闭环改造 + SYSTEM_PROMPT 优化。
3. worker：tracker 推进轮改造 + 删除 decision.py / handle_continue / continue 分支。
4. admin：删除 autoContinue / dispatchContinuePlanStep；新增 plan_advance 事件处理。
5. 测试更新 + E2E 验证。
6. 部署验证（deploy.sh 远端构建，push 后确认 git status 无 ahead 再触发）。
