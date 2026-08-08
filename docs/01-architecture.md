# 01 · 项目架构

> 配套文档：[表结构](02-data-model.md) · [API 设计](03-api.md)
> 定位：将 LSTM 天气预测模型**工具化 · Agent化 · 平台化**的后台管理系统。终端用户用自然语言对话，agent 把已部署的 LSTM 模型当作工具调用来生成预测与分析。

## 1. 总体分层

```
[ 终端用户 ]
    │  HTTPS（管理页面 + 对话 SSE 流）
    ▼
┌──────────────────────────────┐
│  ops-agent-front (Vue3+Vuetify) │  管理后台 + 对话工作台，响应式
└──────────────┬───────────────┘
               │  REST / SSE（/api 代理到 8080）
               ▼
┌──────────────────────────────┐
│  ops-agent-admin (Spring Boot) │  唯一后端入口：认证·权限·模型注册表·数据集·
│  挂载宿主 docker.sock          │  训练编排·对话网关·Docker 生命周期
└──────┬───────────────┬────────┘
       │ Docker API    │  REST（对话转发 / 工具调用）
       ▼               ▼
┌────────────┐  ┌──────────────┐  ┌──────────────────┐
│train 容器   │  │serving容器×N  │  │ agent 服务(常驻)  │
│(LSTM训练·✅)│  │(按版本动态起) │  │ (LangGraph 对话)  │
└──────┬─────┘  └──────┬───────┘  └────────┬─────────┘
       └─────────┬──────┴──────────────────┘
                 ▼
        ┌─────────────────────────────────┐
        │ Postgres（元数据/用户/注册表/对话）│
        │ MinIO（数据集 / 模型产物 对象存储） │
        │ pgvector（agent 记忆 / embedding） │
        └─────────────────────────────────┘
```

## 2. 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vuetify 3（响应式，原生组件优先） |
| 后端 | Spring Boot 3.3（唯一后端入口） |
| 安全 | Spring Security + JWT（jjwt）+ RBAC |
| Agent | Python + LangGraph（后续） |
| 算法 | Python + PyTorch（LSTM 时序预测，CPU 镜像 `pytorch/pytorch:2.3.1-cpu`） |
| 存储 | PostgreSQL + pgvector、MinIO（S3 兼容对象存储） |
| 部署 | Docker Compose（常驻服务 + 动态 training/serving 容器） |

## 3. 服务划分与职责

- **front（Vuetify）**：管理后台（用户/角色/权限/数据集）+ 仪表盘 + 对话工作台（后续）。响应式布局，导航抽屉在桌面常驻、移动端临时。
- **admin（Spring Boot）**：唯一后端入口。JWT 认证；RBAC（用户/角色/权限）；数据集 CRUD；模型注册表；训练编排（已接 Docker API）；serving 编排（已实现：起/停容器 + 就绪轮询 + 代理转发）；对话网关（后续转发 agent）；审计日志（后续）。
- **agent（Python 常驻，后续）**：LangGraph 对话编排；把已部署 LSTM 模型注册为 tool；调 serving 推理 API；记忆写 pgvector。
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

**内置角色**：

| 角色 | 权限 | 说明 |
|------|------|------|
| `ADMIN` | 全部 14 个权限 | 超级管理员 |
| `OPERATOR` | 业务读写 8 个（dataset/model/training/serving 的 read+write），**不含** user/role/permission 后台管理 | 运营人员 |
| `READONLY` | 业务只读 4 个（dataset/model/training/serving 的 read） | 只读用户；未来 agent 智能体的权限继承用户（按用户权限调用工具），故不再设"仅对话"角色 |

**初始账号**：`admin / admin123`（ADMIN）、`user / user123`（OPERATOR，演示运营人员；首次启动由 `DataInitializer` 写入，请上线前修改）。

## 5. 核心数据流（闭环）

数据集上传 → 落 MinIO + 元数据 PG → 后台发起训练（选数据集+超参）→ admin 起 training 容器 → 训练完注册**模型版本**(trained) → 部署某版本 → admin 起 serving 容器并注册 endpoint(deployed) → 终端用户对话提问 → front→admin(SSE)→agent→调 serving tool→返回预测 + 自然语言解读。

## 6. 动态容器编排机制

admin 挂宿主 `/var/run/docker.sock`，用 docker-java 客户端动态起容器，容器加入 compose 自定义网络 `ops-agent-opsnet`。

**训练（已实现）**：触发训练时 admin 先建 `ModelVersion(TRAINING)` + `TrainingJob(PENDING)`，再以 `ops-agent-train:latest` 起一次性容器（命名 `ops-agent-train-job-<jobId>`），通过环境变量注入 MinIO 凭据、数据集 objectKey 与超参。容器保持「哑」——不出网回调 admin，只读写 MinIO 后退出。admin 侧 `@Scheduled(5s)` 轮询容器状态：退出码 0 则读 `models/<mvId>/metrics.json` 回填指标并置 READY，否则置 FAILED；超时强杀；无论成败都抓取 `docker logs` 上传 `artifacts/<jobId>/logs.txt` 后销毁容器。

> 采用轮询而非容器回调：容器无需出网与服务间 token 鉴权，容器崩溃也能靠轮询兜底。

**部署 serving（已实现）**：按模型版本动态起 serving 容器（`ops-agent-serving-<endpointId>`，仅内网），admin 就绪轮询 `/health` 后写 `serving_endpoints`（DEPLOYED），运行期探活标记 UNHEALTHY，下线停删容器；外部经 `/api/serving-proxy/{endpointId}/predict` 代理调用，`/api/serving/tools` 暴露已部署工具供 agent 发现。

## 7. 部署拓扑（docker-compose 规划）

常驻：`front / admin(挂 docker.sock) / postgres(+pgvector) / minio`（agent、redis 后续按需）。所有服务并入自定义网络 `ops-agent-opsnet`。
`train` / `serving` 镜像在 compose 中以 `profiles:["tools"]` 定义，只用于构建出 `ops-agent-train:latest` / `ops-agent-serving:latest`、不随 `up` 启动，实例由 admin 经 Docker API 动态创建。

## 8. 当前实现状态

| 模块 | 状态 |
|------|------|
| Spring Boot 骨架 / JWT / 安全配置 / 全局异常 | ✅ 已实现 |
| RBAC：用户/角色/权限 CRUD + 授权 + 种子数据 | ✅ 已实现 |
| 数据集 CRUD + 文件上传 / Open-Meteo 采集（落 MinIO CSV） | ✅ 已实现 |
| MinIO / pgvector | ✅ 已接入 |
| 训练编排：docker.sock + docker-java + 轮询回填 | ✅ 已实现 |
| 训练模块 `ops-agent-data-train`（PyTorch LSTM，CPU 镜像） | ✅ 已实现 |
| 前端：登录 / 布局 / 仪表盘 / 用户·角色·权限·数据集 | ✅ 已实现 |
| 前端：模型管理 / 训练任务列表 | ✅ 已实现 |
| 部署 serving（推理上线）：data-service 推理服务 + admin 编排 + 代理转发 + 模型服务页 | ✅ 已实现 |
| agent（LangGraph 对话）/ 前端对话工作台 / 审计日志 | ⏳ 后续 |

> 验证：后端 `mvn clean compile` 通过；前端 `vite build` 通过；数据集链路有 pytest E2E（10 passed）；serving 模块有模型加载/预测兼容性测试（pytest）。
