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
users ──< conversations       (user_id)
users ──< audit_logs          (user_id)

datasets ──< model_versions  (dataset_id)
datasets ──< training_jobs   (dataset_id)
model_versions ──< training_jobs      (model_version_id)
model_versions ──< serving_endpoints  (model_version_id)

conversations ──< messages
conversations ──< agent_memories (session_id)
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

> **实现说明**：多轮对话表已落地（conversations / conversation_messages），旧规划的 messages 表名改为 conversation_messages（避免与前端 messages 语义混淆）；agent_memories（pgvector）仍搁置。

### conversations（已建 2026-08-09）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| conversation_id | VARCHAR(64) | UNIQUE NOT NULL | 对外标识（UUID） |
| user_id | BIGINT | FK→users | 归属用户（NULL=系统内部） |
| title | VARCHAR(200) | | 默认"新对话"，首条消息前 20 字 |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

### conversation_messages（已建 2026-08-09）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| message_id | VARCHAR(64) | UNIQUE NOT NULL | 对外标识（UUID） |
| conversation_id | VARCHAR(64) | FK→conversations | |
| role | VARCHAR(16) | | user / assistant / system |
| content | TEXT | | 消息正文（assistant 为 markdown 源文本） |
| reasoning | TEXT | | assistant 推理链全文（可折叠展示） |
| status | VARCHAR(16) | | streaming / completed / failed |
| task_id | VARCHAR(64) | | 该轮内部任务（FK→agent_tasks.task_id） |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

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

### agent_tasks（任务记录）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| task_id | VARCHAR(64) | UNIQUE NOT NULL | uuid |
| task_type | VARCHAR(32) | NOT NULL | question / diagnose_training / diagnose_serving / diagnose_dataset / model_review |
| target_type / target_id | VARCHAR(32) / BIGINT | | 焦点对象（可空） |
| query | TEXT | | 问询原文 / 诊断指令 |
| status | VARCHAR(16) | DEFAULT DISPATCHED | DISPATCHED → RUNNING → SUCCEEDED / FAILED / CANCELLED |
| dispatched_by | BIGINT | FK→users | 触发人（Poller 自动触发可空） |
| worker_id | VARCHAR(64) | | 执行 worker |
| conclusion | TEXT | | TaskResult 结论 |
| started_at / finished_at | TIMESTAMPTZ | | |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_events（任务事件流：进度可观测）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| task_id | VARCHAR(64) | NOT NULL | |
| seq | INT | | 序号 |
| event_type | VARCHAR(16) | | progress / tool_call / error |
| content | TEXT | | 进度文案 / 工具调用 |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_suggestions（处置建议：写操作必须人工确认）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| task_id | VARCHAR(64) | | 来源任务 |
| action_type | VARCHAR(32) | NOT NULL | 对应写工具名（serving_undeploy 等） |
| target_type / target_id | VARCHAR(32) / BIGINT | NOT NULL | 处置目标 |
| params | TEXT | | 业务参数（LLM 填） |
| reason | TEXT | | 建议理由 |
| priority | VARCHAR(8) | DEFAULT NORMAL | HIGH / NORMAL / LOW |
| status | VARCHAR(16) | DEFAULT PENDING | PENDING → APPROVED → EXECUTING → EXECUTED / FAILED；REJECTED；EXPIRED |
| grant_key | VARCHAR(64) | | 确认后签发（审计留痕；Redis 是消费权威） |
| confirmed_by / confirmed_at | BIGINT / TIMESTAMPTZ | | 确认人/时间 |
| executed_at | TIMESTAMPTZ | | 执行时间 |
| result | TEXT | | 执行回执（agent 报告） |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

> grantKey 生命周期（Redis）：`SET agent:grant:{key} = {action, target, suggestionId, workerId} EX 600`；写端点 `@RequireGrant` 校验 action+target 精确匹配 + `GETDEL` 原子消费（一次性）。

## 6. 审计

### audit_logs（规划中）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| user_id | BIGINT | FK→users | |
| action | VARCHAR(64) | | 如 `model:deploy` |
| target_type | VARCHAR(64) | | |
| target_id | BIGINT | | |
| detail | JSONB | | |
| ip | VARCHAR(64) | | |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

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
- `audit_logs(created_at)`。
