# 03 · API 设计

> 配套文档：[项目架构](01-architecture.md) · [表结构](02-data-model.md)
> 基础路径：`/api`。认证：`Authorization: Bearer <JWT>`（登录接口除外）。

## 1. 通用约定

- **内容类型**：JSON（`application/json`）；文件上传用 `multipart/form-data`。
- **分页**：`?page=0&size=20`，响应含 `{content, totalElements, totalPages, number, size}`。
- **错误体**：
  ```json
  { "code": "MODEL_NOT_FOUND", "message": "模型版本不存在", "traceId": "..." }
  ```
- **状态码**：`200/201` 成功；`400` 参数错；`401` 未认证；`403` 无权限；`404` 不存在；`409` 冲突；`422` 业务校验失败；`500` 服务错误。
- **对话流**：`POST /api/chat/stream` 返回 `text/event-stream`（SSE）。

## 2. 认证 Auth

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录，返回 token |
| POST | `/api/auth/refresh` | 刷新 token |
| GET | `/api/auth/me` | 当前用户 |

```jsonc
// POST /api/auth/login
{ "username": "alice", "password": "******" }
// → 200  (ApiResponse.data 内容)
{ "token": "<jwt>", "refreshToken": "<jwt>",
  "userId": 1, "username": "alice", "displayName": "管理员",
  "roles": ["ADMIN"],
  "permissions": ["user:read","user:write","role:read", ...] }
```
> `/api/auth/me` 返回相同结构（token/refreshToken 为 null）。权限点 `permissions` 用于前端菜单/按钮级控制与后端 `@PreAuthorize`。

## 3. 数据集 Datasets

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/datasets` | 列表（分页/筛选 region/status） |
| POST | `/api/datasets/upload` | 上传（multipart：file + name/region/...） |
| GET | `/api/datasets/{id}` | 详情 |
| DELETE | `/api/datasets/{id}` | 删除（连带 MinIO 对象） |

```jsonc
// GET /api/datasets?page=0&size=20
{ "content": [
    { "id": 1, "name": "北京2015-2024气象", "region": "北京",
      "rowCount": 3652, "dateStart": "2015-01-01", "status": "READY" }
  ], "totalElements": 1, "number": 0, "size": 20 }
```

## 4. 模型版本 Models

| 方法 | 路径 | 说明 |
|------|------|------|
| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/models` | `model:read` | 分页列表（`page`/`size`，按 id 倒序） |
| GET | `/api/models/{id}` | `model:read` | 详情（指标 / 超参） |
| POST | `/api/models` | `model:write` | 手动新增版本（实体 JSON） |
| GET | `/api/models/{id}/download` | `model:read` | 返回 `models/<id>/model.pt` 的 MinIO 预签名 URL（`expiryMinutes` 默认 30） |
| DELETE | `/api/models/{id}` | `model:write` | 删除 |

模型状态流转：`TRAINING`（训练触发时创建）→ `READY`（容器退出码 0，指标已回填）/ `FAILED`。

```jsonc
// GET /api/models/1
{ "id": 1, "name": "北京气温LSTM", "version": "v1",
  "datasetId": 1, "algorithm": "LSTM",
  "hyperparameters": "{\"seqLen\":30,\"hiddenSize\":64,\"lr\":0.001,\"epochs\":50}",
  "metrics": "{\"mae\":1.82,\"rmse\":2.41,\"loss\":0.031,\"epochs\":50}",
  "status": "READY", "artifactKey": "models/1/model.pt" }

// GET /api/models/1/download → { "url": "http://minio:9000/models/1/model.pt?X-Amz-..." }
```

## 5. 训练 Training

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/training/jobs` | `training:write` | 创建任务：建 ModelVersion + Job，随即起训练容器，立即返回 |
| GET | `/api/training/jobs` | `training:read` | 分页列表 |
| GET | `/api/training/jobs/{id}` | `training:read` | 任务详情（状态 / 容器 ID / 起止时间） |
| GET | `/api/training/jobs/{id}/logs` | `training:read` | 返回 `artifacts/<jobId>/logs.txt` 预签名 URL（任务结束后才有） |
| DELETE | `/api/training/jobs/{id}` | `training:write` | 删除任务记录 |

任务状态流转：`PENDING` → `RUNNING`（容器已启动）→ `SUCCEEDED` / `FAILED`（由 admin `@Scheduled(5s)` 轮询容器退出码回填；超时会强杀置 FAILED）。

```jsonc
// POST /api/training/jobs
{ "datasetId": 1, "name": "北京气温LSTM", "version": "v1", "algorithm": "LSTM",
  "hyperparameters": { "seqLen": 30, "hiddenSize": 64, "epochs": 50, "batchSize": 32, "lr": 0.001 } }
// → 200
{ "id": 10, "status": "RUNNING", "modelVersionId": 1,
  "containerId": "8f3c...", "startedAt": "2026-08-08T13:20:11Z" }
```

## 6. 部署 Serving

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/serving/deploy` | 部署某版本 → 起 serving 容器（当前为基础版，接收 `ServingEndpoint` 实体，`status` 置 `DEPLOYING`/`DEPLOYED`） |
| POST | `/api/serving/endpoints/{id}/undeploy` | 卸载 |
| GET | `/api/serving/endpoints` | endpoint 列表（分页） |
| GET | `/api/serving/tools` | 供 agent 使用的工具清单（仅返回 `status=DEPLOYED` 的端点，含 `name=lstm_predict`、`url`、`endpointId`） |

> **当前实现状态**：`/api/datasets`、`/api/models`、`/api/training/jobs` 均为完整实现（训练已接 Docker 动态编排 + 轮询回填）。`/api/serving` 仍为接口桩，直接以实体 JSON 收发，尚未实现推理容器拉起逻辑。

```jsonc
// POST /api/serving/deploy
{ "modelVersionId": 1 }
// → 201
{ "id": 5, "modelVersionId": 1, "url": "http://ops-serve-5:8000/predict",
  "status": "DEPLOYING" }

// GET /api/serving/tools
[ { "name": "lstm_predict", "description": "基于历史天气的 LSTM 预测",
    "parameters": { "region": "string", "metric": "string",
                    "startDate": "date", "days": "int" },
    "endpointId": 5 } ]
```

## 7. 对话（SSE）Conversation

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/stream` | SSE 流式对话 |

```jsonc
// 请求体
{ "conversationId": 1, "message": "北京接下来 7 天气温趋势如何？" }
```

SSE 事件流：
```
event: token
data: {"content": "根据"}

event: tool_call
data: {"tool": "lstm_predict", "args": {"region": "北京", "metric": "气温", "days": 7}}

event: token
data: {"content": "已部署模型预测，"}

event: done
data: {"conversationId": 1}
```

> agent 内部流程：意图识别 → 抽参调用 `lstm_predict` tool → 取 serving 返回 → LLM 生成解读 → 流式回传；多轮上下文经 pgvector 检索。

## 8. 审计 Audit

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/audit/logs` | 审计日志（ADMIN） |

```jsonc
// GET /api/audit/logs?page=0&size=20
{ "content": [
    { "id": 99, "userId": 1, "action": "model:deploy",
      "targetType": "model_version", "targetId": 1,
      "ip": "10.0.0.5", "createdAt": "2026-08-07T20:30:00Z" }
  ], "totalElements": 1 }
```

## 9. serving 容器推理接口（内部）

agent 调用，不对外暴露：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/predict` | LSTM 推理 |

```jsonc
// POST /predict
{ "region": "北京", "metric": "气温", "startDate": "2026-08-08", "days": 7 }
// → 200
{ "region": "北京", "metric": "气温",
  "forecast": [ { "date": "2026-08-08", "value": 28.4 },
                { "date": "2026-08-09", "value": 29.1 } ] }
```
