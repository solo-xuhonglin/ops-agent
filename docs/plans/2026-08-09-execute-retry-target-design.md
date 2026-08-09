# execute 失败容错 + 目标信息进模型上下文（2026-08-09）

> 决策人：@xuhonglin · 状态：已确认（两节均通过）· 前置：grant 已简化为一次性凭证（7281743）

## 1. 背景

- **环节 B 无容错**：approve → execute 直调写工具失败（400，如参数值错误）→ 只做 LLM 总结 + suggestion 置 FAILED → 结束。
  模型没有机会看到错误并修正参数，用户"点了确认没反应"。
- **target 信息缺失**：`approve_<写工具>` schema 只有写工具业务参数 + plan_id/step_no/retry_of，
  没有 target_type/target_id 字段 → 建议落库 target 恒为空/0，前端展示"训练任务:0"。
  用户明确：目标信息应由**模型在上下文携带**（提建议时填写），不靠代码从 params 解析推导。

## 2. 设计

### 2.1 容错：execute 失败 → 失败决策轮（复用 decision 机制）

```
approve → execute 执行 → 业务失败（4xx/5xx）
  → suggestion 置 FAILED（保留审计，结论=失败总结）
  → 触发失败决策轮（decision.py 新增，复用 bind_tools + ToolMessage 循环）
      模型输入：失败原因（工具返回原文）+ 原建议参数 + 当前 plan 上下文
      模型动作：
        A. 参数可修正 → 调 approve_<写工具>(修正后参数, retry_of=原suggestion_id)
           → 落新 PENDING 建议 → 前端可见 → 用户再次审批 ✅（修正仍过人工）
        B. 方案不可行 → 输出放弃说明（可 plan_update 标记）
        C. 临时故障/无法自行修正 → 输出说明等待人工
  → 决策文本并入 execute 结论（send_result）
```

- **复用**：`run_decision_round` 的循环骨架（bind_tools → tool_calls 执行 → ToolMessage 回填 → 收敛），
  新增 `run_failure_decision(llm_runtime, http, registry, client, store, ctx, failure_context, original_suggestion)`，
  失败提示词（FAILURE_DECISION_SYSTEM）区别于 DECISION_SYSTEM。
- **安全**：修正参数的新建议仍走 PENDING → 人工审批，不绕过授权；retry_of 关联原建议（前端可展示"重试"语义）。
- **前端**：execute 失败后，approve/reject 的轮询刷新（已实现）能拉到新 PENDING 建议，用户可见可操作。

### 2.2 target 信息进模型上下文

- `_build_approve_schema`：parameters 追加（可选字段）：
  ```json
  "target_type": {"type": "string", "enum": ["dataset","training_job","model_version","serving_endpoint"],
                  "description": "操作目标类型"},
  "target_id": {"type": "integer", "description": "操作目标对象 ID"}
  ```
- `handle_suggest_action` 落库：直接取 `args.target_type / args.target_id`（逻辑已有，模型现在能填）。
- **不**从 params 反推 target（如 datasetId → dataset:3）——目标由模型携带，代码只透传。
- plan_create 的 steps 目标语义不变，两侧统一。

## 3. 实施清单

1. worker `graph.py`：`_build_approve_schema` 追加 target_type/target_id；确认 handle_suggest_action 透传
2. worker `decision.py`：新增 `run_failure_decision` + `FAILURE_DECISION_SYSTEM`
3. worker `core.py`：`handle_execute` 失败分支（业务 4xx/5xx）→ 触发失败决策轮 → 决策文本并入结论
4. 测试：test_decision / test_agent_core 补充失败决策用例；mock LLM 适配
5. commit + push → 远端部署 agent → E2E 验证（制造参数错误 → 确认出现 retry 新建议）

## 4. 风险

| 风险 | 缓解 |
|---|---|
| 失败决策轮模型乱调工具 | 复用 decision 循环上限 MAX_DECISION_ROUNDS；仅 approve_*/plan_update/只读工具可用 |
| 无限重试循环 | retry 建议仍是 PENDING 人工审批，不会自动重执行；execute 每次失败只触发一轮决策 |
| target 字段模型不填 | 可选字段，不填则保持现状（空 target），不影响功能 |
