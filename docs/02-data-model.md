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
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

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

## 4. 对话与记忆

### conversations
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| user_id | BIGINT | FK→users | |
| title | VARCHAR(255) | | |
| created_at / updated_at | TIMESTAMPTZ | DEFAULT now() | |

### messages
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| conversation_id | BIGINT | FK→conversations | |
| role | VARCHAR(16) | | user / assistant / system / tool |
| content | TEXT | | |
| tool_call | JSONB | | 工具调用记录（名/参/结果） |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

### agent_memories（pgvector）
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | |
| session_id | VARCHAR(128) | | 对应 conversation_id |
| content | TEXT | | 记忆原文 |
| embedding | vector(1536) | | 维度随 LLM embedding 配置 |
| metadata | JSONB | | |
| created_at | TIMESTAMPTZ | DEFAULT now() | |

> embedding 维度与所选 LLM 的 embedding 模型一致，由 ENV 配置，建表时按实际维度调整。

## 5. 审计

### audit_logs
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

## 6. MinIO 目录约定

```
datasets/   <dataset_id>/<file>           # 原始天气数据集
models/     <model_version_id>/            # 训练产物（.pt / 配置 / scaler）
artifacts/  <training_job_id>/logs.txt     # 训练日志、评估图、导出
```

## 7. 索引建议

- 外键列建索引：`datasets(created_by)`、`model_versions(dataset_id, status)`、`training_jobs(status)`、`serving_endpoints(status)`、`messages(conversation_id)`、`agent_memories(session_id)`。
- `agent_memories` 对 `embedding` 建 ivfflat / hnsw 向量索引（按 pgvector 版本）。
- `audit_logs(created_at)`、`conversations(user_id, updated_at)`。
