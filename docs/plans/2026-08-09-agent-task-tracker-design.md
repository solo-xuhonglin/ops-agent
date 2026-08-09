# Agent 侧任务跟踪设计（Plan + 自主推进）

> 2026-08-09，替代 admin 侧关系表/定时器的方案。

## 目标

异步写操作（训练/部署）由 **agent 侧自主跟踪推进**：第一次对话建立多步 Plan，
每步执行后 agent 更新 Plan 并自主决定是否轮询/推进下一步。业务层纯净（只校验
授权 key），admin 零触发机制（无关系表、无定时器、无 followup 接口）。

## 核心流程

```
用户: "用96训练并部署"
  ↓
agent 分析 → 建立 Plan（conversation 级，agent 内存）:
    { conversationId, steps: [
      { action: training_create, status: awaiting_approval },
      { action: serving_deploy,  status: pending } ] }
  ↓ 当前任务推第一个 suggestion（training_create）
用户: 审批
  ↓
execute_suggestion 任务 → training_create(带 grantKey) → jobId=32
  ↓ agent 更新 Plan: step1 → executing(jobId=32)
  ↓ TaskTracker.register(jobId=32, 目标=SUCCEEDED, 下一步=部署评估)
  ↓ 自主轮询 training_get(32)：10s 起步指数退避，5m 封顶（凭证=长 TTL 任务 token）
训练完成
  ↓ TaskTracker 检测 → 更新 Plan: step1 → done
  ↓ 检查下一步可执行 → 【gRPC 上报】async_suggestion(serving_deploy)
  ↓ admin 落 agent_suggestions(PENDING) → 用户前端看到审批卡
用户: 审批 → execute_suggestion → serving_deploy → endpointId
  ↓ 跟踪 endpoint → READY → Plan 全部 done → 汇报
```

## 组件

### proto（admin + core 同步 + 重新生成 stubs）
- `TaskDispatch` 加 `conversation_id`（字段 8）：agent 知道自己在哪个会话（Plan key）
- `ClientMessage` 加 `async_suggestion`（`AsyncSuggestion` 消息）：
  `conversation_id / task_id(来源) / action_type / target_type / target_id / params / reason / priority`
  —— agent 跟踪器推进 Plan 时上报，admin 落 `agent_suggestions(PENDING)`

### admin
- `dispatch` 填 `TaskDispatch.conversation_id`（AgentTask.conversationId 已有）
- `AgentGrpcService` 处理 `async_suggestion` → 落库 PENDING（带 conversation_id + task_id 关联）
- `execute_suggestion` 任务 scoped token TTL 延长（配置 `agent.execute-token-ttl-seconds`，默认 24h；其他任务保持 5min）
- 删除：`conversation_links` 表 + `ConversationLinkAspect` + `TrainingFollowupService`
- 保留：`AgentTask.result_object_type/id`（响应记录 + 审计 + 恢复依据）

### agent（core）
- `TaskTracker`（内存，conversationId → Plan）：
  - `plan()`：第一次对话建 Plan（步骤列表）
  - `register(objectType, objectId, step)`：写接口响应后注册监视
  - 后台轮询循环：10s 起步指数退避（×2 到 5m 封顶），调业务查询（training_get/serving_get）
  - 目标达成 → 更新 Plan 进度 → 有下一步则 `send_async_suggestion`（GrpcClient）
  - 终态（FAILED）→ 记录失败，可选上报
- **历史恢复**：对话任务（question）开始时先调 `agent_task_list`（已有工具）查本会话任务，
  对 result_object_id 非空且已 SUCCEEDED 的任务，用业务工具查对象状态 → 重建未完成的 Plan 步骤并继续推进
- 凭证：跟踪轮询复用 execute_suggestion 任务 scoped token（长 TTL，只读查询；写仍需 grantKey）

## 删除/收敛清单
- `conversation_links` 表 + `ConversationLinkAspect`
- `TrainingFollowupService`（admin 定时扫描）
- （无 followup REST 接口、无 messages API 触发）

## 边界
- worker 重启丢 Plan/跟踪（内存态）；恢复机制见上（agent_task_list + 对象状态重建）
- 长 TTL token 不扩大写权限（写仍需 grantKey）
