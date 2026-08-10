# ops-agent · 算法资产平台

> 将 LSTM 天气预测模型 **工具化 · 平台化 · Agent 化** 的后台管理系统：数据集 → 训练 → 模型版本 → serving 部署 → 预测，全链路闭环。
> **AI Agent（ops-agent-core）为内部运维/业务自动化助手**：监督训练任务、serving 健康、数据集/模型质量，异常时诊断并产出处置建议，人工确认后自动执行。

## 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                    ops-agent-front (Vue 3 + Vuetify 4)            │
│  管理后台：仪表盘 · 数据集 · 模型 · 训练 · Serving · 用户/角色/权限 │
│  Agent 助手浮窗：对话 · 建议审批 · 历史 · Plan 卡片               │
└──────────────────────────┬───────────────────────────────────────┘
                           │ REST API (/api)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│               ops-agent-admin (Spring Boot 3.3)                  │
│  认证 · RBAC · 数据集 CRUD · 训练编排 · Serving 编排 · 审计日志   │
│  gRPC Server (:9090) · Agent 工具执行器 · 建议审批 · grantKey    │
│  挂载 docker.sock（动态起 training/serving 容器）                │
└──────┬──────────────────────────────┬────────────────────────────┘
       │ Docker API                   │ gRPC 双向流 ◄── 出站拨号
       ▼                              ▼
┌──────────────┐  ┌───────────────┐  ┌─────────────────────────────┐
│ training 容器 │  │ serving 容器×N │  │ ops-agent-core (Python)     │
│ (LSTM 训练)   │  │ (按版本动态起) │  │ LangGraph 决策图            │
│ 一次性，跑完  │  │ 常驻，仅内网   │  │ ChatDeepSeek(deepseek-v4)   │
│ 即回收       │  │               │  │ 零监听端口，纯出站 gRPC     │
└──────┬───────┘  └───────┬───────┘  └─────────────┬───────────────┘
       └─────────┬────────┴──────────┬──────────────┘
                 ▼                   ▼
        ┌─────────────────────────────────────┐
        │ PostgreSQL · MinIO(S3) · Redis       │
        │ 元数据/用户/任务/建议 · 数据/模型/日志│
        │ · grantKey 权威存储                  │
        └─────────────────────────────────────┘
```

**核心通信模型**：agent 为 gRPC **client 出站拨号** admin 的 gRPC server（内网 `:9090`），**零监听端口、物理上不暴露任何接口**。一条双向流承载：注册（Register/RegisterAck，含动态工具 schema 下发）、任务下发（TaskDispatch）、事件回推（TaskEvent）、结果（TaskResult）、grantKey 推送（AuthorizationGrant）、心跳（Ping/Pong）。断线指数退避重连（1s→30s+抖动），重连后自动重新注册。

## 关键特征

### 数据闭环
- **数据集管理**：上传 CSV / Open-Meteo 天气采集 → 落 MinIO + 元数据 PostgreSQL → 支持 CSV/Parquet 格式
- **模型训练**：一键发起 LSTM 训练 → admin 通过 docker.sock 动态拉起训练容器 → 训练产物回传 MinIO → 指标自动回填
- **模型部署**：选择模型版本部署 → 动态拉起 FastAPI serving 容器（仅内网） → 就绪轮询 → 代理转发推理请求
- **推理预测**：经 `/api/serving-proxy/{endpointId}/predict` 代理调用，支持单步/多步递归预测

### AI Agent 自动化
- **LangGraph 决策图**：agent 决策节点 ↔ tools 执行节点循环，LLM 自主决定调哪些工具，支持多轮推理（thinking/reasoning_effort）
- **动态工具注册**：工具 schema 由 RegisterAck **动态下发**（能力=admin 现有 API，agent 零硬编码），加工具=改库不改代码
- **多轮对话**：前端全局浮窗，跨路由常驻，支持 SSE 流式回显、深度思考、停止生成、历史回溯
- **Plan 多步规划**：agent 自主拆解复杂任务为多步 plan，每步产出建议，逐步审批推进，自动轮询等待（wait_until）
- **消息存储**：Agent 端 MessageStore 按 LangGraph 轮次批量写入，Java 端仅写入 USER 和 APPROVAL 消息，SSE 纯转发

### 安全模型
- **任务级 scoped token**：派发任务时签发 JWT（权限裁剪、TTL 5min），代码层注入 `Authorization` 头，LLM 不可见不可改
- **写操作「建议 + 人工确认 + 时效 grantKey」**：写工具一律先产出建议（PENDING）→ 人工确认 → 签发时效 grantKey（Redis，TTL 600s，一次性 `GETDEL`）→ agent 持 key 执行 → `@RequireGrant` AOP 校验精确匹配
- **身份标识代码注入**：`X-Agent-Worker` / `X-Agent-Task` 头由代码统一注入，LLM 只填业务参数，prompt injection 触不到鉴权链路

### RBAC 权限管理
- 经典 `users ↔ user_roles ↔ roles ↔ role_permissions ↔ permissions` 五表模型
- 内置 8 个权限域（user/role/permission/dataset/model/training/serving/agent）× 读写共 16 个权限码
- 内置 ADMIN / OPERATOR / READONLY 三个角色，前端菜单/按钮级控制

### 审计与运维
- **审计日志**：写操作自动记录（人类经 AuditInterceptor，agent 经 GrantCheckAspect），参数脱敏
- **远程运维脚本**：`scripts/` 目录下基于 paramiko 的 SSH 运维套件，支持按服务粒度部署、状态巡检、构建监控
- **一键部署**：`deploy.sh` 全程服务器内拉取 → 编译 → 构建 → 启动，支持按服务/跳过编译/强制重建等灵活选项

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vuetify 4（管理后台 + 全局 Agent 助手浮窗） |
| 后端 | Spring Boot 3.3（唯一后端入口）+ gRPC（agent 双向流）+ Spring Security + JWT |
| Agent | Python 3.11 + **LangGraph**（决策图）+ ChatDeepSeek（deepseek-v4-flash，原生 thinking + function calling） |
| 算法 | Python + PyTorch（LSTM 时序预测，CPU 镜像） |
| 存储 | PostgreSQL（元数据）、MinIO（数据集/模型/日志三桶）、Redis（grantKey 权威存储） |
| 部署 | Docker Compose（常驻服务 + 动态拉起的 training / serving 容器） |

## 目录结构

```
ops-agent/
├── README.md                     # 本文档
├── docs/
│   ├── 01-architecture.md        # 项目架构
│   ├── 02-data-model.md          # 表结构
│   ├── 03-api.md                 # API 设计
│   ├── 04-deploy.md              # 部署指南
│   └── 05-ops-scripts.md         # 运维脚本
├── ops-agent-front/              # Vue 3 + Vuetify 4 前端
├── ops-agent-admin/              # Spring Boot 后端（总入口 / gRPC server / 建议管理）
├── ops-agent-core/               # Python Agent worker（LangGraph 决策图，gRPC 出站拨号）
├── ops-agent-data-train/         # LSTM 训练容器（MinIO 输入 / 产物）
├── ops-agent-data-service/       # LSTM 推理服务容器（FastAPI）
├── scripts/                      # 远程运维脚本（paramiko SSH）
├── docker-compose.yml            # Docker Compose 编排
├── deploy.sh                     # 一键部署脚本
└── .env.example                  # 环境变量模板
```

## 文档导航

- [项目架构](docs/01-architecture.md)
- [表结构](docs/02-data-model.md)
- [API 设计](docs/03-api.md)
- [部署指南](docs/04-deploy.md)
- [运维脚本](docs/05-ops-scripts.md)

## 状态

✅ **已闭环**：数据 → 训练 → 模型版本 → serving 部署 → 代理预测；**Agent 全链路**：gRPC 长连接（M1）→ LangGraph 决策 + 动态工具 schema（M2）→ 处置建议 + grantKey 授权闭环（M3）→ 前端助手浮窗（M4）→ Plan 多步规划 + 消息存储重构（M5）。

| 模块 | 里程碑 |
|------|--------|
| 数据集 CRUD + 天气采集 + MinIO 集成 | ✅ M0 |
| 训练编排（docker.sock + docker-java + 轮询回填） | ✅ M0 |
| Serving 部署（动态容器 + 健康轮询 + 代理转发） | ✅ M0 |
| gRPC 双向流 / 注册 / 心跳 / 重连 / 任务闭环 | ✅ M1 |
| LangGraph 决策图 + DeepSeek function calling + 动态工具 schema + scoped token | ✅ M2 |
| 处置建议闭环（建议表 + approve/reject + grantKey + 写工具执行） | ✅ M3 |
| 前端 Agent 助手浮窗（FAB + 抽屉 + 建议卡片 + 历史视图 + 分析按钮） | ✅ M4 |
| Plan 多步规划（plan_create/plan_update + wait_until 轮询 + 步骤推进） | ✅ M5 |
| Agent 消息存储重构（MessageStore 按轮次批量写入，Java 端清理流式落库） | ✅ M5 |
| agent 任务中断恢复（checkpoint 持久化 saver）/ 多 agent | ⏳ 后续 |

> 初始账号：`admin / admin123`（ADMIN，全部权限）、`user / user123`（OPERATOR，业务读写；上线前请修改密码）。