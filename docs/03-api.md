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
| GET | `/api/models` | 列表（含 status） |
| GET | `/api/models/{id}` | 详情（指标 / 超参） |
| POST | `/api/models/{id}/register` | **训练容器回调**：注册版本为 TRAINED |

```jsonc
// GET /api/models/1
{ "id": 1, "name": "北京气温LSTM", "version": "v1",
  "datasetId": 1, "algorithm": "LSTM",
  "hyperparameters": { "seqLen": 30, "hiddenSize": 64, "lr": 0.001, "epochs": 50 },
  "metrics": { "mae": 1.82, "rmse": 2.41, "mape": 0.09 },
  "status": "TRAINED", "artifactKey": "models/1/model.pt" }
```

## 5. 训练 Training

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/training/jobs` | 创建任务 → admin 起 training 容器 |
| GET | `/api/training/jobs` | 列表 |
| GET | `/api/training/jobs/{id}` | 状态 + 日志 URL |
| POST | `/api/training/jobs/{id}/stop` | 停止容器 |

```jsonc
// POST /api/training/jobs
{ "datasetId": 1, "modelName": "北京气温LSTM", "version": "v1",
  "hyperparameters": { "seqLen": 30, "hiddenSize": 64, "lr": 0.001, "epochs": 50 } }
// → 201
{ "id": 10, "status": "PENDING", "modelVersionId": 1 }
```

## 6. 部署 Serving

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/serving/deploy` | 部署某版本 → 起 serving 容器（当前为基础版，接收 `ServingEndpoint` 实体，`status` 置 `DEPLOYING`/`DEPLOYED`） |
| POST | `/api/serving/endpoints/{id}/undeploy` | 卸载 |
| GET | `/api/serving/endpoints` | endpoint 列表（分页） |
| GET | `/api/serving/tools` | 供 agent 使用的工具清单（仅返回 `status=DEPLOYED` 的端点，含 `name=lstm_predict`、`url`、`endpointId`） |

> **当前实现状态（基础版）**：`/api/models`、`/api/training/jobs`、`/api/serving` 为接口桩，直接以实体 JSON 收发（便于后续接入 Docker 动态编排），尚未实现容器拉起逻辑。`/api/datasets` 为完整 CRUD。

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
