# 02 · 表结构

> 配套文档：[项目架构](01-architecture.md) · [API 设计](03-api.md)
> 约定：主键 `BIGSERIAL`；时间统一 `TIMESTAMPTZ`（默认 `now()`）；外键命名 `<表>_id`。

## 1. ER 关系概览

```
users ──< user_roles >── roles ──< role_permissions >── permissions

users ──< datasets            (created_by)
users ──< model_versions      (trained_by)
users ──< training_jobs       (triggered_by)
users ──< serving_endpoints   (deployed_by)
users ──< agent_conversations (user_id)
users ──< audit_logs          (user_id)

datasets ──< model_versions  (dataset_id)
datasets ──< training_jobs   (dataset_id)
model_versions ──< training_jobs      (model_version_id)
model_versions ──< serving_endpoints  (model_version_id)

agent_conversations ──< agent_conversation_messages
agent_conversations ──< agent_plans
```

## 2. 用户与权限

### users
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| username | VARCHAR(64) | UNIQUE, NOT NULL | 登录名 |
| password_hash | VARCHAR(255) | NOT NULL | BCrypt |
| display_name | VARCHAR(128) | | 展示名 |
| email | VARCHAR(128) | | |
| status | VARCHAR(16) | DEFAULT 'ACTIVE' | ACTIVE / DISABLED |
| created_at / updated_at | TIMESTAMPTAMPTZ | DEFAULT now() | |
| created_by | BIGINT | FK→users, nullable | 创建者用户 ID（自引用，可空） |

### roles
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| name | VARCHAR(64) | UNIQUE, NOT NULL | ADMIN / OPERATOR / USER |
| description | VARCHAR(255) | | |

### permissions
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| code | VARCHAR(64) | UNIQUE, NOT NULL | 如 `dataset:write` |
| description | VARCHAR(255) | | |

### user_roles
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| user_id | BIGINT | PK, FK→users | |
| role_id | BIGINT | PK, FK→roles | |

### role_permissions
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| role_id | BIGINT | PK, FK→roles | |
| permission_id | BIGINT | PK, FK→permissions | |

## 3. 数据集 / 模型 / 训练 / 部署

### datasets
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| name | VARCHAR(128) | NOT NULL | |
| description | TEXT | | |
| object_key | VARCHAR(512) | NOT NULL | MinIO `datasets/` 下 key |
| region | VARCHAR(64) | | 天气地区 |
| source | VARCHAR(64) | | 数据源描述 |
| file_format | VARCHAR(32) | | csv / parquet |
| row_count | BIGINT | | |
| date_start / date_end | DATE | | 数据时间范围 |
| status | VARCHAR(16) | DEFAULT 'READY' | UPLOADING / READY / INVALID |
| created_by | BIGINT | FK→users | |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

### model_versions
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| name | VARCHAR(128) | NOT NULL | 模型展示名 |
| version | VARCHAR(32) | NOT NULL | v1 / v20260807 |
| dataset_id | BIGINT | FK→datasets | 训练数据集 |
| algorithm | VARCHAR(64) | DEFAULT 'LSTM' | |
| hyperparameters | JSONB | | seq_len / hidden_size / lr / epochs … |
| metrics | JSONB | | mae / rmse / mape … |
| artifact_key | VARCHAR(512) | | MinIO `models/` 下 key |
| status | VARCHAR(16) | | TRAINED / DEPLOYED / ARCHIVED / INVALID |
| trained_by | BIGINT | FK→users | |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |
| UNIQUE | | (name, version) | 同名同版本唯一 |

### training_jobs
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| model_version_id | BIGINT | FK→model_versions（回调后填充） | |
| dataset_id | BIGINT | FK→datasets | |
| container_id | VARCHAR(128) | | Docker 容器 ID |
| status | VARCHAR(16) | | PENDING / RUNNING / SUCCEEDED / FAILED / STOPPED |
| triggered_by | BIGINT | FK→users | |
| log_key | VARCHAR(512) | | MinIO 训练日志 key |
| started_at / finished_at | TIMESTAMPTZ | | |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

### serving_endpoints
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| model_version_id | BIGINT | FK→model_versions | |
| container_id | VARCHAR(128) | | Docker 容器 ID |
| host | VARCHAR(128) | | 容器名 / service |
| port | INT | | 动态分配端口 |
| url | VARCHAR(255) | | 内部地址 `http://<host>:<port>/predict` |
| status | VARCHAR(16) | | DEPLOYING / DEPLOYED / STOPPED / ERROR |
| deployed_by | BIGINT | FK→users | |
| created_at / stopped_at | TIMESTAMPTZ | | |

## 4. 对话与记忆（2026-08-09 对话已实现，记忆仍搁置）

> **实现说明**：多轮对话表已落地（agent_conversations / agent_conversation_messages），旧名 conversation_messages 已迁移为 agent_conversation_messages。agent_memories（pgvector）仍搁置。

### agent_conversations（已建 2026-08-09）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| conversation_id | VARCHAR(64) | UNIQUE NOT NULL | 对外标识（UUID） |
| user_id | BIGINT | FK→users | 归属用户（NULL=系统内部） |
| title | VARCHAR(200) | | 默认"新对话"，首条消息前 20 字 |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_conversation_messages（已建 2026-08-09，2026-08-10 重构消息存储）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| message_id | VARCHAR(64) | UNIQUE NOT NULL | 对外标识（Agent 生成 `round_<taskId>_<roundIndex>` / Java 生成 UUID） |
| conversation_id | VARCHAR(64) | FK→agent_conversations | |
| kind | VARCHAR(16) | NOT NULL | USER / ASSISTANT / TOOL_CALL / TOOL_RESULT / APPROVAL |
| role | VARCHAR(16) | | user / assistant / tool / approval（@PrePersist 从 kind 派生，兼容历史 SQL） |
| content | TEXT | | 消息正文 |
| reasoning | TEXT | | ASSISTANT 推理链全文（可折叠展示） |
| status | VARCHAR(16) | | completed / streaming / failed |
| task_id | VARCHAR(64) | | 该轮内部任务（FK→agent_tasks.task_id） |
| tool_call_id | VARCHAR(64) | | TOOL_CALL ↔ TOOL_RESULT 配对（同一 LLM 原生 tool_call 共享 call_id） |
| tool_name | VARCHAR(64) | | TOOL_CALL/TOOL_RESULT 的工具名（如 dataset_list） |
| tool_args | TEXT | | TOOL_CALL 的入参 JSON 字符串 |
| tool_summary | TEXT | | TOOL_RESULT 的截断结果摘要（≤500 字符） |
| payload_json | TEXT | | APPROVAL 的结构化数据：建议快照 + 审批结果（JSON） |
| decision | VARCHAR(16) | | APPROVAL 审批结果（PENDING/APPROVED/REJECTED/EXECUTED/FAILED/EXPIRED） |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

**消息存储职责**：
- **Agent 端（MessageStore）**：写入 ASSISTANT、TOOL_CALL、TOOL_RESULT（按 LangGraph 轮次批量 `ON CONFLICT (message_id) DO UPDATE`）
- **Java 端（JPA）**：写入 USER（`send()` 时）、APPROVAL（`saveApprovalDecision()` 时）、plan_update 助理消息

### agent_plans（已建 2026-08-09）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| plan_id | VARCHAR(64) | UNIQUE NOT NULL | UUID |
| conversation_id | VARCHAR(64) | FK→agent_conversations | 所属会话 |
| summary | VARCHAR(255) | | 计划摘要 |
| steps | TEXT | | 步骤清单 JSON（worker 直写；模型掌舵每步状态） |
| status | VARCHAR(16) | DEFAULT 'PLANNED' | PLANNED / RUNNING / DONE / FAILED / CANCELLED |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_memories（未建，pgvector）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| session_id | VARCHAR(128) | | 对应 conversation_id |
| content | TEXT | | 记忆原文 |
| embedding | vector(1536) | | 维度随 LLM embedding 配置 |
| metadata | JSONB | | |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

> embedding 维度与所选 LLM 的 embedding 模型一致，由 ENV 配置，建表时按实际维度调整。

## 5. Agent 任务 / 事件 / 建议 / 工具（2026-08-08 已实现）

### agent_tools（工具注册表：能力=数据，admin 注册时动态下发 schema）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| name | VARCHAR(64) | UNIQUE NOT NULL | 工具名（下划线，如 `dataset_list`；DeepSeek 拒含点） |
| description | TEXT | NOT NULL | 给 LLM 的工具描述 |
| http_method | VARCHAR(8) | NOT NULL | GET/POST/DELETE |
| path_template | VARCHAR(255) | NOT NULL | 现有 REST API 路径模板（`{jobId}` 占位） |
| auth_permission | VARCHAR(64) | | 权限点（如 `training:read`） |
| is_write | BOOLEAN | DEFAULT FALSE | 写工具需 grantKey 授权 |
| params_schema | TEXT | NOT NULL | OpenAI 格式 JSON Schema（仅业务参数） |
| enabled | BOOLEAN | DEFAULT TRUE | |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_tasks（任务记录：chat 对话轮 / execute 执行轮）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| task_id | VARCHAR(64) | UNIQUE NOT NULL | uuid |
| task_type | VARCHAR(24) | NOT NULL | chat（对话轮）/ execute（执行已审批建议） |
| plan_id | VARCHAR(64) | | 所属 plan（execute 有；chat 可为空） |
| suggestion_id | VARCHAR(64) | | execute 对应建议 |
| conversation_id | VARCHAR(64) | | 所属会话 |
| query | TEXT | | 问询原文 |
| status | VARCHAR(16) | DEFAULT DISPATCHED | DISPATCHED → RUNNING → SUCCEEDED / FAILED / CANCELLED |
| worker_id | VARCHAR(64) | | 执行 worker |
| conclusion | TEXT | | TaskResult 结论 |
| reasoning | TEXT | | LLM 推理链全文（chat 轮） |
| started_at / finished_at | TIMESTAMPTZ | | |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_events（任务事件流：进度可观测）（已废弃，由 SSE 流替代）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| task_id | VARCHAR(64) | NOT NULL | |
| seq | INT | | 序号 |
| event_type | VARCHAR(16) | | progress / tool_call / error |
| content | TEXT | | 进度文案 / 工具调用 |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_suggestions（处置建议：写操作必须人工确认，plan 的步骤 + 审批对象）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| suggestion_id | VARCHAR(64) | UNIQUE NOT NULL | UUID（worker 生成；approve/执行回写用它） |
| plan_id | VARCHAR(64) | | 所属 plan（多步规划；null=非规划的单条建议） |
| step_no | INT | | plan 内步骤顺序（1..N） |
| source_task_id | VARCHAR(64) | | 来源任务（chat 轮 / 触发决策的 execute 轮） |
| conversation_id | VARCHAR(64) | NOT NULL | 所属会话 |
| action_type | VARCHAR(32) | NOT NULL | 对应写工具名（training_create / serving_deploy 等） |
| target_type / target_id | VARCHAR(32) / BIGINT | | 处置目标 |
| params | TEXT | | 业务参数（JSON 字符串，LLM 填） |
| reason | TEXT | | 建议理由 |
| priority | VARCHAR(8) | DEFAULT NORMAL | HIGH / NORMAL / LOW |
| status | VARCHAR(16) | DEFAULT PENDING | PENDING → APPROVED → EXECUTING → EXECUTED / FAILED；PENDING → REJECTED / EXPIRED / CANCELLED |
| grant_key | VARCHAR(128) | | 确认后签发（审计留痕；Redis 是消费权威） |
| confirmed_by / confirmed_at | BIGINT / TIMESTAMPTZ | | 确认人/时间 |
| executed_at | TIMESTAMPTZ | | 执行时间 |
| result | TEXT | | 执行回执（LLM 总结，worker 直写） |
| retry_of | VARCHAR(64) | | 重试来源建议（决策轮 retry 时挂） |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

> grantKey 生命周期（Redis）：`SET agent:grant:{key} = {action, target, suggestionId, workerId} EX 600`；写端点 `@RequireGrant` 校验 action+target 精确匹配 + `GETDEL` 原子消费（一次性）。

## 6. 审计

### audit_logs（2026-08-09 已实现）
系统写入（人类写操作经 AuditInterceptor、agent 写操作经 GrantCheckAspect），无写接口。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| action | VARCHAR(64) | NOT NULL | 写操作码：`dataset:create` / `serving:deploy` … |
| actor_type | VARCHAR(16) | NOT NULL | `USER` / `AGENT`（即"是否 agent 执行"） |
| actor_name | VARCHAR(128) | | 执行人：人类用户名 或 `Agent` |
| approver_name | VARCHAR(128) | | agent 写操作的审批人（人类） |
| target_type | VARCHAR(64) | | 操作对象类型 |
| target_id | BIGINT | | 操作对象 id |
| params | JSONB | | 参数（人类=请求体脱敏；agent=建议 params） |
| ip | VARCHAR(64) | | 来源 IP |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

> 已刻意精简：去掉 `actor_user_id` / `approver_user_id` / `task_id` / `worker_id` / `success`，溯源靠 `params` + `target`。

## 7. MinIO 目录约定（多桶）

```
桶 datasets：datasets/   <dataset_id>/<file>      # 原始天气数据集（weather.csv / 上传文件）
桶 models：  models/     <model_version_id>/      # 训练产物（model.pt / metrics.json / scaler）
桶 logs：    logs/       <training_job_id>/logs.txt  # 训练日志
```

> 桶名可配：`MINIO_BUCKET` / `MINIO_MODEL_BUCKET` / `MINIO_LOG_BUCKET`。遗留：历史空 `artifacts` 桶可删。

## 8. 索引建议

- 外键列建索引：`datasets(created_by)`、`model_versions(dataset_id, status)`、`training_jobs(status)`、`serving_endpoints(status)`、`agent_tasks(status)`、`agent_suggestions(status)`、`agent_events(task_id)`。
- `agent_tools(name)` 唯一索引（建表已带）。
- `audit_logs(created_at)`、`audit_logs(actor_type)`、`audit_logs(action)`。
