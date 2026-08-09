# Agent 模块重构设计：plan = 模型掌舵的备忘录 + 观察决策闭环

> 2026-08-09 用户拍板：agent 侧是智能体主逻辑（task 自己写库），admin 只做对话通信 + 审批。
> 本设计是 v3 重构的收敛版：修复"plan_create 自动落建议"的隐藏副作用导致的重复审批卡。

## 1. 问题背景

截图复现：模型调用 `plan_create` 后，又自己调了 2 次 `suggest_action(dataset_create)`，
前端堆出 5 张待审批卡。根因：

- `plan_create` 内部**一次性为所有步骤落 PENDING suggestion**（隐藏副作用）
- 提示词却告诉模型"plan_create 只是记录计划、审批走 suggest_action"
- 模型不知道工具的副作用 → 重复建议

**修复原则：工具零隐藏副作用、职责单一、plan 状态由模型掌舵。**

## 2. 数据模型

```
agent_plans      + steps TEXT（JSON）   ← 新增列：步骤清单备忘本体（LLM 传入原样存）
agent_suggestions  不变                 ← 串行：同一时刻至多一条 plan 步骤处于 PENDING
```

- `plan_create` 只落 plan 行（summary + steps[] + status），**不落任何 suggestion**
- 步骤建议由模型逐步用 `suggest_action(plan_id, step_no)` 提出

## 3. 工具集（worker 内置，职责单一、零隐藏副作用）

| 工具 | 职责 | 副作用 |
|---|---|---|
| `plan_create` | 建规划备忘录（summary + steps[]） | 仅落 plan 行 |
| `plan_update` | 更新 plan 状态 + 任一步骤状态（done/failed/cancelled + 说明） | 更新 plan/步骤状态 + plan_update 通知 |
| `suggest_action` | 提出一条写操作建议（可挂 plan_id + step_no） | 落 PENDING 建议，审批后系统执行 |
| （只读工具） | 观察真实状态 | 无 |

## 4. 模型驱动的完整循环

```
① chat 轮：模型观察（dataset_list）→ plan_create(summary, steps[3])   ← 只建备忘录
② 模型：suggest_action(plan_id, step_no=1, training_create, dataset:96)
③ 用户 approve → execute（系统直调，异步）→ Monitor 轮询【观察】
④ 轮询到达终态 → 【决策轮】拉起 LLM：
      看对象终态 → plan_update(step1=done)
      → 自行判断：suggest_action(step_no=2, serving_deploy)
         / plan_update(DONE|FAILED|CANCELLED) / 重试(retry_of)
⑤ approve step2 → execute → 观察 → 决策轮 → … → 全部 done → plan_update(DONE)
```

**串行由模型保证**：按 steps 顺序逐步 suggest_action，上一步观察完成（done）后才提下一步。

## 5. 决策轮（观察后再决定）

- **触发**：Monitor 轮询到达终态（SUCCEEDED/FAILED/CANCELLED/STOPPED）
- **执行**：单轮 LLM 调用（同一套工具协议），输入 plan 上下文 + 对象终态/原因
- **决策面**：`plan_update`（步骤 done/failed、plan DONE/FAILED/CANCELLED）→ `suggest_action`（下一步/重试 retry_of）→ 或仅 report
- **安全边界**：写操作一律 `suggest_action` → 审批 → 执行，**重试同样要审批**
- **防死循环**：决策轮工具循环上限 3 轮

## 6. 系统职责最终形态

| 层 | 职责 |
|---|---|
| **模型** | plan 生命周期掌舵：建规划、更新步骤状态、决定下一步 |
| **worker 系统** | 执行已审批写操作 + Monitor 轮询观察 + 触发决策轮 |
| **admin** | 审批（approve/reject/expire）+ 对话通信 + 只读查询 |

## 7. 前端

- plan 卡片读 `plan.steps` 清单 + 按 step_no 关联 suggestions 标状态：已完成/执行中/待审批/等待中（灰）
- plan_update 事件刷新 plan 卡片 + 落对话消息

## 8. 实施顺序

| 阶段 | 内容 |
|---|---|
| P1 | SQL（agent_plans + steps 列）+ task_store（upsert_plan 存 steps / plan_update / suggestion 挂 plan） |
| P2 | graph：plan_create 零建议副作用 + 新增 plan_update handler + suggest_action 支持 plan_id/step_no |
| P3 | tracker：_on_done/_on_failed 改为触发决策轮（注入 llm）；决策轮实现 |
| P4 | 前端 plan 卡片（等待中步骤）+ 提示词更新 |
| P5 | 测试（plan_create 零副作用 / plan_update / 决策轮 / 串行）+ 部署 |

## 9. 测试要点

- plan_create 零建议副作用断言 + instruction 返回
- plan_update 状态更新（plan 级 + 步骤级）
- 决策轮：Monitor 终态 → 决策 stub → 断言 plan_update/suggest_action 调用序列
- 串行：step1 未 done 前模型不提 step2（决策轮行为断言）
