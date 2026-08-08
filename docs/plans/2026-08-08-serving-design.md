# 模型服务（serving）设计（2026-08-08）

> 配套：brainstorming 流程产出；实现覆盖 `ops-agent-data-service` / `ops-agent-admin` / `ops-agent-front` / 部署脚本。
> 目标：把训练产物（`models/<mvId>/model.pt`）封装成**独立推理服务** `ops-agent-data-service`，由 admin 编排动态拉起 serving 容器并对外（经 admin 代理）提供预测能力；前端提供「模型服务」管理页 + 测试入口。
> 阶段定位：项目早期、测试/开发中，**代码以最新状态为准，不向前兼容**。

---

## 1. 关键决策（已与用户拍板）

| # | 议题 | 结论 |
|---|------|------|
| 1 | 服务形态 | **轻量 FastAPI 自研**（python:3.11-slim + CPU torch），与训练模块同构，不用 TorchServe/Triton |
| 2 | 预测能力 | **单步 + 多步递归**：`POST /predict` 传历史序列（长度 ≥ seq_len）+ 可选 `horizon`（默认 1，上限 168）；horizon>1 时服务端递归滚雪球，返回长度 = horizon 的数组 |
| 3 | 网络暴露 | **仅内网 + admin 代理**：serving 容器只加入 `ops-agent-opsnet`、不映射宿主端口；外部一律走 `POST /api/serving-proxy/{endpointId}/predict`（admin 复用 JWT + RBAC 鉴权后内网转发） |
| 4 | 生命周期 | **admin 动态拉容器**（docker-java，复用 TrainingLauncher 模式）：容器名 `ops-agent-serving-<endpointId>`，就绪轮询 `/health`，下线停删容器；前端提供管理界面 |
| 5 | 职责边界 | **data-service 与部署流程完全独立**：data-service 是"哑推理服务"，只认环境变量（模型来源）+ 暴露 HTTP 接口，不感知容器名/网络/健康检查；起容器、命名、探活、状态机、代理转发、界面全部在 admin 侧实现。手动 `docker run` 也能直接跑 |
| 6 | 鉴权 | **admin 代理层鉴权**（JWT + `serving:read`），serving 容器自身不设鉴权，隔离靠 Docker 内网 |
| 7 | 多实例 | **允许多版本并存**：每个模型版本可独立部署一个 serving 容器，endpoint 独立管理、可分别下线（A/B 对比、多地区模型共存） |
| 8 | 界面形态 | 模型列表（READY）行内「部署」按钮触发 → 跳转独立「模型服务」页（侧边栏菜单）统一管理 + 测试推理入口 |

---

## 2. 职责边界（核心架构约定）

```
┌─────────────────────────────────────────────────────────────┐
│ ops-agent-data-service（独立推理服务，不感知编排）              │
│   环境变量: MINIO_* / MODEL_BUCKET / MODEL_VERSION_ID        │
│   启动: 下载 model.pt → 加载 LSTM(state_dict+归一化参数)       │
│   接口: GET /health · POST /predict                          │
└─────────────────────────────────────────────────────────────┘
                     ▲ 由 admin 动态拉起（docker-java）
┌─────────────────────────────────────────────────────────────┐
│ ops-agent-admin（编排层，与 data-service 独立）               │
│   ServingLauncher（起/停容器、命名、加网络）                   │
│   ServingHealthPoller（就绪轮询 + 运行期探活 + 状态机）        │
│   ServingProxyController（/api/serving-proxy/{id}/predict 转发）│
│   ServingEndpoint（PG 表，记录 containerId/url/status）        │
└─────────────────────────────────────────────────────────────┘
```

- **解耦契约**：admin 只依赖"镜像名 + 环境变量约定 + `/health` 语义"；data-service 只依赖"环境变量 + HTTP"。任一侧可独立替换。
- **对调用方透明**：外部 URL 永远是 `/api/serving-proxy/{endpointId}/predict`，无论 serving 容器重启、重建，URL 不变，只是内部转发目标变化。

---

## 3. 服务模块 `ops-agent-data-service`

**技术栈**：Python 3.11 + FastAPI + uvicorn + boto3 + CPU torch（安装套路复用训练镜像：torch 走 `download.pytorch.org/whl/cpu`，其余走腾讯 PyPI 镜像）。

**文件**：
- `Dockerfile`：`FROM python:3.11-slim`，`CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8000"]`
- `requirements.txt`：`fastapi, uvicorn, boto3, torch(单独源), numpy`
- `serve.py`：FastAPI 应用，全部配置从环境变量读

**环境变量清单**（admin 起容器时注入）：
```
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=****
MINIO_SECRET_KEY=****
MODEL_BUCKET=models
MODEL_VERSION_ID=<mvId>
```

**启动流程**（容器内部）：读环境变量 → 从 MinIO 下载 `models/<mvId>/model.pt` → `torch.load` 解析 `{state_dict, hyperparameters{seq_len,hidden_size,mean,std}}` → 构建 `LSTMModel(hidden_size)` 并 `load_state_dict` → 置 `model.eval()` → 起 FastAPI。**加载失败（MinIO 拉取失败/文件损坏/state_dict 不匹配）→ 启动即异常退出（非 0），由 admin 判定 FAILED**。

**推理接口**：

`GET /health` →
```json
{ "status": "ok", "modelVersionId": "12" }
```

`POST /predict` →
```json
// 请求
{ "values": [20.1, 20.5, 21.0, 20.8], "horizon": 24 }
// 响应
{ "predictions": [25.3, 25.9, 26.4], "modelVersionId": "12" }
```

- **单步**（horizon=1）：`values` 尾部取 seq_len 个 → 归一化（(x-mean)/std）→ 过模型 → 反归一化还原 → 输出 1 个值。
- **多步递归**（horizon>1）：每次把上一步输出拼接回窗口尾部继续预测（滚雪球），循环 horizon 次。
- **参数校验**：`values` 长度 < seq_len → 400；含非数值 → 400；`horizon` 缺省 1、≤0 或 >168 → 400。
- 日志打印英文（沿用项目约定：`log.info(...)` 英文）。

---

## 4. 后端编排层（`ops-agent-admin`）

### 4.1 `ServingLauncher`（新增，复用 TrainingLauncher 模式）

- **部署**：`ServingEndpoint(status=CREATING)` → docker-java `createContainer` 起 `ops-agent-serving:latest`，容器名 `ops-agent-serving-<endpointId>`，`network=ops-agent-opsnet`，注入 §3 环境变量 → `start` → 返回 containerId。
- **下线**：`stopAndRemove(endpointId)` 停删容器。
- 配置项：`serving.enabled` / `serving.image` / `serving.network` / `serving.ready-timeout-seconds`（默认 60）。

### 4.2 `ServingHealthPoller`（新增）

- **就绪轮询**：部署后每 ~2s `GET http://<容器名>:8000/health`，成功 → 回填 `containerId/url`（`http://ops-agent-serving-<endpointId>:8000`）置 `DEPLOYED`；超时 → 置 `FAILED` 并清理容器。
- **运行期探活**：`@Scheduled` 定期对 `DEPLOYED` endpoint 探活，连续失败标记 `UNHEALTHY`（可配置阈值），恢复则回 `DEPLOYED`。

### 4.3 状态机

```
CREATING → DEPLOYED（就绪）/ FAILED（超时或加载失败）
DEPLOYED → UNHEALTHY（探活失联）→ DEPLOYED（恢复）/ FAILED
DEPLOYED/UNHEALTHY → STOPPING（下线中）→ STOPPED
```

### 4.4 API 契约

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/serving/endpoints` | `serving:read` | endpoint 列表（分页） |
| GET | `/api/serving/endpoints/{id}` | `serving:read` | endpoint 详情 |
| POST | `/api/serving/deploy` | `serving:write` | body `{modelVersionId}`；校验模型存在且 READY → 触发 ServingLauncher |
| DELETE | `/api/serving/endpoints/{id}` | `serving:write` | 下线：停删容器 + 置 STOPPED |
| POST | `/api/serving-proxy/{endpointId}/predict` | `serving:read` | JWT 鉴权 → 查 endpoint（不存在 404、非 DEPLOYED 409）→ 内网转发 `POST http://<容器名>:8000/predict` → 透传 |
| GET | `/api/serving/tools` | `serving:read` | 保留：列 DEPLOYED endpoint，供 agent 发现（已存在） |

- `ServingController` 现有 `/deploy` 桩升级为真正调用 `ServingLauncher`。
- `ServingEndpoint` 实体已存在（modelVersionId/containerId/host/port/url/status/deployedBy/createdAt/stoppedAt），字段够用，无需迁移。

---

## 5. 前端两处改动（`ops-agent-front`）

### 5.1 模型页（`views/models/ModelList.vue`）

- READY 状态模型行内新增「部署」按钮（`mdi-rocket-launch`，受 `serving:write` 控制，非 READY 禁用）。
- 点击 → `POST /api/serving/deploy` → toast「已提交部署」→ 跳转「模型服务」页。

### 5.2 模型服务页（`views/serving/ServingList.vue`，perm `serving:read`）

- 表格列：ID / 模型版本 / 状态（CREATING·DEPLOYED·UNHEALTHY·FAILED·STOPPING·STOPPED chip）/ 容器 / URL / 创建时间 / 操作。
- 操作：`部署`（弹窗选 READY 模型）、`下线`（confirmDialog 确认）、`测试推理`。
- **测试推理弹窗**：输入历史序列（逗号分隔数值）+ horizon → `POST /api/serving-proxy/{id}/predict` → 展示预测值列表（纯文本/数值卡片，本期不画曲线）。
- 路由 `/serving`，侧边栏「模型服务」（`mdi-server`），沿用 `meta.perm` 门禁 + `menus` 按 `auth.hasPerm` 过滤。
- 状态徽标沿用现有 chip 风格（CREATING=info / DEPLOYED=success / UNHEALTHY=warning / FAILED=error / STOPPED=grey）。

---

## 6. 部署

- `docker-compose.yml`：新增 `serving` 服务定义（`profiles:["tools"]`，`build: ./ops-agent-data-service`，产出 `ops-agent-serving:latest`），不随 `up` 启动，实例由 admin 动态创建。
- admin 环境变量追加：`SERVING_ENABLED` / `SERVING_IMAGE` / `SERVING_NETWORK` / `SERVING_READY_TIMEOUT_SECONDS`。
- `deploy.sh`：加 `docker compose --profile tools build serving` 预构建镜像。
- `docs/04-deploy.md`：更新部署拓扑，补 serving 编排说明。

---

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| 模型加载失败（MinIO 拉取/文件损坏/state_dict 不匹配） | 容器启动异常退出非 0 → admin 就绪轮询判定 FAILED + 清理容器 |
| 就绪轮询超时（容器起不来/端口未监听） | endpoint FAILED + 清理容器 |
| `/predict` 参数非法 | serving 返回 400（JSON error），经代理透传 |
| 代理转发：endpoint 不存在 | 404 |
| 代理转发：endpoint 非 DEPLOYED（含 UNHEALTHY） | 409 |
| 代理转发：内网请求超时/连接失败 | 502（admin 侧超时如 15s） |
| 下线时容器已不存在 | 幂等：忽略异常，直接置 STOPPED |

---

## 8. 测试

- **data-service 单测**（pytest）：模型加载（构造最小 state_dict）、单步预测、多步递归长度/值校验、归一化往返、参数校验 400 分支。可在无 MinIO 环境用本地临时模型文件测加载逻辑（环境变量支持 `MODEL_FILE` 本地路径覆盖 MinIO 下载，便于测试）。
- **admin 集成**（pytest E2E，参照现有训练链路测试）：部署（mock 或真实 docker）→ 就绪轮询 → 代理 predict → 下线 → 状态机断言。
- **冒烟**：compose 起 serving 后 `curl /health` + 一次 `/predict`。

---

## 9. 与既有文档/代码的差异说明

- `docs/01-architecture.md` 第 6 节"部署 serving（后续）"：本方案落地为 admin 动态拉容器 + 内网代理 + 前端管理页，与架构文档方向一致；待实现后同步更新架构文档实现状态表。
- 训练管道设计（`2026-08-08-model-training-pipeline.md`）第 10 节"serving 部署上线"被本期落地。
- `ServingEndpoint` 实体/`ServingController` 骨架已存在，本期在其上补真正编排逻辑，不另起炉灶。

---

## 10. 后续可扩展（非本期）

- agent（LangGraph）经 `/api/serving/tools` + 代理接口把 LSTM 注册为工具调用；测试推理前端画预测曲线（ECharts）；serving 容器资源限制（CPU/mem）；GPU 镜像；serving 缓存/批处理。
