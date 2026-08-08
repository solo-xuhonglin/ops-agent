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
| DELETE | `/api/training/jobs/{id}` | `training:write` | 删除任务记录（先停删运行中容器，并清理 MinIO 日志） |

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

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/serving/endpoints` | `serving:read` | endpoint 列表（分页） |
| GET | `/api/serving/endpoints/{id}` | `serving:read` | endpoint 详情 |
| POST | `/api/serving/deploy` | `serving:write` | 部署：body `{modelVersionId}`（须 READY）→ 起 serving 容器 → 返回 CREATING 端点，就绪由轮询判定 |
| POST | `/api/serving/endpoints/{id}/undeploy` | `serving:write` | 下线：停删容器 → 置 STOPPED（记录保留，供审计） |
| DELETE | `/api/serving/endpoints/{id}` | `serving:write` | 物理删除记录：先停删容器再删记录（幂等） |
| POST | `/api/serving-proxy/{endpointId}/predict` | `serving:read` | 推理代理：JWT 鉴权后内网转发给对应 serving 容器（非 DEPLOYED 返回 409） |
| GET | `/api/serving/tools` | `serving:read` | 供 agent 使用的工具清单（仅 `status=DEPLOYED` 的端点，含 `name=lstm_predict`、`url`、`endpointId`） |

> **当前实现状态**：`/api/datasets`、`/api/models`、`/api/training/jobs`、`/api/serving` 均为完整实现。serving 已接 Docker 动态编排（`ServingLauncher` 起容器 + `ServingHealthPoller` 就绪轮询/探活 + 状态机 CREATING→DEPLOYED/FAILED→STOPPING→STOPPED/UNHEALTHY），推理统一经 `/api/serving-proxy` 代理，serving 容器自身不设鉴权（仅内网）。

```jsonc
// POST /api/serving/deploy
{ "modelVersionId": 1 }
// → 200
{ "id": 5, "modelVersionId": 1, "status": "CREATING",
  "containerId": null, "host": null, "port": null, "url": null }
// 就绪轮询成功后：
{ "id": 5, "modelVersionId": 1, "status": "DEPLOYED",
  "containerId": "a1b2...", "host": "ops-agent-serving-5",
  "port": 8000, "url": "http://ops-agent-serving-5:8000" }

// POST /api/serving-proxy/5/predict
{ "values": [20.1, 20.5, 21.0, 20.8, 19.9], "horizon": 3 }
// → 200
{ "predictions": [20.7, 20.9, 21.1], "modelVersionId": "1" }

// GET /api/serving/tools
[ { "name": "lstm_predict", "description": "基于历史天气的 LSTM 预测",
    "endpointId": 5, "url": "http://ops-agent-serving-5:8000" } ]
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

serving 容器（`ops-agent-serving-<endpointId>:8000`）仅加入内网 `opsnet`，不映射宿主端口，不直接对外暴露；由 admin 的 `/api/serving-proxy/{endpointId}/predict` 转发调用：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 就绪/探活：`{"status":"ok","modelVersionId":"1"}` |
| POST | `/predict` | LSTM 推理（单步/多步递归） |

```jsonc
// POST /predict
{ "values": [20.1, 20.5, 21.0, 20.8, 19.9, 20.3], "horizon": 7 }
// → 200
{ "predictions": [20.7, 20.9, 21.1, 21.4, 21.6, 21.8, 21.9],
  "modelVersionId": "1" }
```

> 语义约定：`values` 为历史气温序列（单位 ℃，长度 ≥ 模型 seq_len），`horizon` 为未来预测步数（1–168）；参数非法返回 400，模型加载失败时容器启动即异常退出（由 admin 判定 FAILED）。
