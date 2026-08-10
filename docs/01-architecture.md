# 01 · 项目架构

> 配套文档：[表结构](02-data-model.md) · [API 设计](03-api.md) · [部署](04-deploy.md)
> 定位：算法资产平台（LSTM 天气预测模型：数据集 → 训练 → 模型版本 → serving 部署 → 预测）。
> **AI Agent（ops-agent-core）为内部运维/业务自动化助手**：监督训练、serving 健康、数据集/模型质量，异常时诊断并产出处置建议（人工确认后自动执行），不面向终端用户对话。

## 1. 总体分层

```
[ 运维/运营人员 ]
    │  HTTPS（管理后台 + Agent 助手浮窗）
    ▼
┌──────────────────────────────┐
│  ops-agent-front (Vue3+Vuetify4)│ 管理后台 + 全局 Agent 助手浮窗（跨路由常驻）
└──────────────┬───────────────┘
               │  REST（/api 代理到 8080）
               ▼
┌──────────────────────────────┐
│  ops-agent-admin (Spring Boot) │  唯一后端入口：认证·权限·数据集·模型·训练·
│  挂载宿主 docker.sock          │  serving 编排·gRPC Server(:9090 内网)·
│                               │  agent 工具执行器(鉴权)/建议确认/grantKey
└──────┬───────────────┬────────┘
       │ Docker API    │  gRPC 双向流 ◄──出站拨号──┐（agent 零监听端口）
       ▼               ▼                         │
┌────────────┐  ┌──────────────┐  ┌──────────────────┐
│train 容器   │  │serving容器×N │  │ ops-agent-core     │
│(LSTM训练)   │  │(按版本动态起) │  │ (Python worker,    │
└──────┬─────┘  └──────┬───────┘  │  LangGraph 决策)   │
       └─────────┬──────┴──────────┴────────┬─────────┘
                 ▼                          │ HTTP 调 admin 现有 API（工具）
        ┌─────────────────────────────────┐ │（带 scoped taskToken / grantKey）
        │ Postgres（元数据/用户/任务/建议） │◄┘
        │ Redis（grantKey 权威存储）       │
        │ MinIO（数据集 / 模型产物 / 日志） │
        └─────────────────────────────────┘
```

**核心通信模型（Agent）**：
- agent 为 gRPC **client 出站拨号** admin 的 gRPC server（内网 `:9090`，不映射宿主）—— agent **零监听端口、物理上不暴露任何接口**。
- 一条双向流承载：注册（Register/RegisterAck，含动态工具 schema 下发）、任务下发（TaskDispatch）、事件回推（TaskEvent）、结果（TaskResult）、grantKey 推送（AuthorizationGrant）、心跳（Ping/Pong）。
- 断线指数退避重连（1s→30s+抖动），重连后自动重新注册；admin 侧 WorkerRegistry 心跳 90s 超时清理。

## 2. 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vuetify 4（响应式，原生组件优先） |
| 后端 | Spring Boot 3.3（唯一后端入口）+ gRPC(grpc-server-spring-boot-starter) |
| 安全 | Spring Security + JWT（jjwt）+ RBAC + 任务级 scoped token + 时效 grantKey |
| Agent | Python 3.11 + **LangGraph**（决策图）+ langchain-deepseek(ChatDeepSeek→deepseek-v4-flash) + grpcio |
| LLM | DeepSeek（OpenAI 兼容 API，`deepseek-v4-flash`，支持原生 function calling + thinking/reasoning_effort，key 走服务器 .env） |
| 算法 | Python + PyTorch（LSTM 时序预测，CPU 镜像 `pytorch/pytorch:2.3.1-cpu`） |
| 存储 | PostgreSQL、MinIO（S3 兼容对象存储）、Redis（grantKey） |
| 部署 | Docker Compose（常驻服务 + 动态 training/serving 容器） |

## 3. 服务划分与职责

- **front（Vuetify）**：管理后台（用户/角色/权限/数据集/模型/训练/serving）+ 仪表盘 + **全局 Agent 助手浮窗**（右下 FAB + 右侧抽屉，跨路由常驻：对话视图跟踪任务事件流、历史视图查看任务与处置建议、输入框上方滑出 PENDING 建议卡片、底部输入框自然语言问询）。
- **admin（Spring Boot）**：唯一后端入口。JWT 认证；RBAC；数据集 CRUD + Open-Meteo 采集；模型注册表；训练编排（docker.sock + docker-java）；serving 编排（起/停容器 + 就绪轮询 + 代理转发）；**gRPC server（:9090 内网）**；**agent 能力执行器**（工具=现有 REST API，鉴权后执行）；**处置建议管理**（approve 签发 grantKey 推 agent + 派发执行任务）。
- **agent（Python 常驻，零端口）**：gRPC client 出站拨号 admin；LangGraph 决策图（agent 决策节点 ↔ tools 执行节点循环，LLM 自主决定调哪些工具）；工具 schema 由 RegisterAck **动态下发**（能力=admin 现有 API，agent 零硬编码）；纯被动响应（admin 下发任务才行动）。
  - **Plan 多步规划**：Agent 通过 `plan_create`/`plan_update` 工具自主拆解复杂任务为多步 plan，步骤以 `agent_suggestions` 记录（`plan_id` + `step_no`），每步产出写操作建议，人工审批后推进；`wait_until` 工具支持轮询等待对象状态就绪，全部步骤完成自动置 plan DONE。plan 变更经 `plan_update` 事件通知前端 SSE 刷新 plan 卡片。
  - **消息存储**：Agent 端 MessageStore 按 LangGraph 轮次批量写入 `agent_conversation_messages` 表（ASSISTANT/TOOL_CALL/TOOL_RESULT），Java 端仅写入 USER（发消息时）和 APPROVAL（审批时），SSE 事件纯转发不落库。
- **training（Python 动态容器，已实现）**：从 MinIO 拉数据集 CSV→预处理→训 PyTorch LSTM→评估→产物（`model.pt` / `metrics.json`）存 MinIO→退出；状态由 admin 轮询回填。
- **serving（Python 按版本动态容器，已实现）**：加载指定模型版本→暴露 `/health` + `/predict`（单步/多步递归）→admin 代理 `/api/serving-proxy/{endpointId}/predict` 对外转发。

## 4. RBAC 权限模型

基于 `users ↔ user_roles ↔ roles ↔ role_permissions ↔ permissions` 的经典 RBAC。
后端用 `@PreAuthorize("hasAuthority('xxx')")` 做方法级鉴权；前端用 `auth.hasPerm()` 控制菜单/按钮。

**内置权限码（seed 数据生成）**：

| 域 | 读 | 写 |
|----|----|----|
| 用户 | `user:read` | `user:write` |
| 角色 | `role:read` | `role:write` |
| 权限 | `permission:read` | `permission:write` |
| 数据集 | `dataset:read` | `dataset:write` |
| 模型 | `model:read` | `model:write` |
| 训练 | `training:read` | `training:write` |
| 部署 | `serving:read` | `serving:write` |
| **Agent** | `agent:read` | `agent:write` |

**内置角色**：

| 角色 | 权限 | 说明 |
|------|------|------|
| `ADMIN` | 全部 16 个权限 | 超级管理员 |
| `OPERATOR` | 业务读写（dataset/model/training/serving 的 read+write + agent:read/write），**不含** user/role/permission 后台管理 | 运营人员 |
| `READONLY` | 业务只读（dataset/model/training/serving 的 read + agent:read） | 只读用户 |

**初始账号**：`admin / admin123`（ADMIN）、`user / user123`（OPERATOR，演示运营人员；首次启动由 `DataInitializer` 写入，请上线前修改）。

## 5. 核心数据流（闭环）

数据集上传 → 落 MinIO + 元数据 PG → 后台发起训练（选数据集+超参）→ admin 起 training 容器 → 训练完注册模型版本 → 部署某版本 → admin 起 serving 容器并注册 endpoint → **Agent 介入**：admin 侧 Poller/用户检测到异常 → 派发诊断任务 → agent 决策图自主调工具（查询/日志/健康）→ 产出结论 + 处置建议（落库 PENDING）→ 前端浮窗展示 → 人工确认 → admin 签发时效 grantKey（Redis）推 agent → agent 持 key 调写工具执行 → 结果回写建议状态。

## 6. Agent 安全模型（关键）

- **任务级 scoped token**：派发任务时 admin 签发 JWT（`type=scoped`，权限裁剪为任务所需、TTL 5min），随 TaskDispatch 下发；agent 调 HTTP 工具时由 `http_client` 代码层注入 `Authorization`，filter 免查库直接读 claims。
- **身份标识代码注入**：`X-Agent-Worker` / `X-Agent-Task` 头由代码统一注入，LLM 只填业务参数、系统参数不可见不可改（prompt injection 触不到鉴权链路）。
- **写操作「建议 + 人工确认 + 时效 grantKey」**：写工具一律先产出建议（PENDING）；人工确认 → admin 签发 `agent:grant:{key}`（Redis，TTL 600s）沿 gRPC 推 agent → agent 调写工具带 `X-Grant-Key` → admin `@RequireGrant` AOP 校验（action+target 精确匹配 + GETDEL 原子消费）→ 执行。无 key / target 不匹配 → 403（实测 LLM 擅自改目标被正确拒绝）。
- **工具注册表存库**：`agent_tools` 表（名称/描述/HTTP 映射/权限点/JSON Schema），注册时动态下发，加工具=改库不改代码。

## 7. 动态容器编排机制

admin 挂宿主 `/var/run/docker.sock`，用 docker-java 客户端动态起容器，容器加入 compose 自定义网络 `ops-agent-opsnet`。

**训练（已实现）**：触发训练时 admin 先建 `ModelVersion(TRAINING)` + `TrainingJob(PENDING)`，再以 `ops-agent-train:latest` 起一次性容器（命名 `ops-agent-train-job-<jobId>`），通过环境变量注入 MinIO 凭据、数据集 objectKey 与超参。容器保持「哑」——不出网回调 admin，只读写 MinIO 后退出。admin 侧 `@Scheduled(5s)` 轮询容器状态：退出码 0 则读 `models/<mvId>/metrics.json` 回填指标并置 READY，否则置 FAILED；超时强杀；无论成败都抓取 `docker logs` 上传 `logs/<jobId>/logs.txt` 后销毁容器。

> 采用轮询而非容器回调：容器无需出网与服务间 token 鉴权，容器崩溃也能靠轮询兜底。

**部署 serving（已实现）**：按模型版本动态起 serving 容器（`ops-agent-serving-<endpointId>`，仅内网），admin 就绪轮询 `/health` 后写 `serving_endpoints`（DEPLOYED），运行期探活标记 UNHEALTHY，下线停删容器；外部经 `/api/serving-proxy/{endpointId}/predict` 代理调用。

## 8. 部署拓扑（docker-compose）

常驻：`front / admin(挂 docker.sock) / postgres / minio / redis / agent`。所有服务并入自定义网络 `ops-agent-opsnet`。
`train` / `serving` 镜像在 compose 中以 `profiles:["tools"]` 定义，只用于构建出 `ops-agent-train:latest` / `ops-agent-serving:latest`、不随 `up` 启动，实例由 admin 经 Docker API 动态创建。
agent/redis 均为**常驻、零端口映射**（agent 唯一外呼：DeepSeek API）。

## 9. 当前实现状态

| 模块 | 状态 |
|------|------|
| Spring Boot 骨架 / JWT / 安全配置 / 全局异常 | ✅ 已实现 |
| RBAC：用户/角色/权限 CRUD + 授权 + 种子数据 | ✅ 已实现 |
| 数据集 CRUD + 文件上传 / Open-Meteo 采集（落 MinIO CSV） | ✅ 已实现 |
| MinIO 多桶（datasets/models/logs） | ✅ 已实现 |
| 训练编排：docker.sock + docker-java + 轮询回填 | ✅ 已实现 |
| 训练模块 `ops-agent-data-train`（PyTorch LSTM，CPU 镜像） | ✅ 已实现 |
| 部署 serving：data-service 推理 + admin 编排 + 代理转发 + 服务页 | ✅ 已实现 |
| **Agent 通信层**（gRPC 双向流/注册/心跳/重连/任务闭环） | ✅ 已实现（M1） |
| **Agent 决策层**（LangGraph 图 + DeepSeek function calling + 动态工具 schema + scoped token） | ✅ 已实现（M2） |
| **处置建议闭环**（建议表 + approve/reject + grantKey(Redis) + 写工具执行） | ✅ 已实现（M3） |
| **前端 Agent 助手浮窗**（FAB + 抽屉 + 建议卡片 + 历史视图 + 分析按钮） | ✅ 已实现（M4） |
| **Agent 消息存储重构**（MessageStore 按轮次批量写入，Java 端清理流式落库） | ✅ 已实现（M5） |
| **Plan 多步规划**（plan_create/plan_update + wait_until 轮询 + 步骤推进） | ✅ 已实现（M5） |
| agent 任务中断恢复（checkpoint 持久化 saver）/ 多 agent | ⏳ 后续（MemorySaver 已就位，换持久化 saver 即可） |

> 验证：后端 `mvn compile` 通过（IDEA）；前端 `vite build` 通过；Python pytest 25 绿；服务器 E2E：派发 question → DeepSeek 自主调 dataset_list → 结论落库 SUCCEEDED；处置建议 approve → grantKey → agent 执行写工具 → EXECUTED。
