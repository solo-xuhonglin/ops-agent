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
│training容器 │  │serving容器×N  │  │ agent 服务(常驻)  │
│(PyTorch训练)│  │(按版本动态起) │  │ (LangGraph 对话)  │
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
| 算法 | Python + PyTorch（LSTM 时序预测，后续） |
| 存储 | PostgreSQL + pgvector、MinIO（S3 兼容对象存储） |
| 部署 | Docker Compose（常驻服务 + 动态 training/serving 容器） |

## 3. 服务划分与职责

- **front（Vuetify）**：管理后台（用户/角色/权限/数据集）+ 仪表盘 + 对话工作台（后续）。响应式布局，导航抽屉在桌面常驻、移动端临时。
- **admin（Spring Boot）**：唯一后端入口。JWT 认证；RBAC（用户/角色/权限）；数据集 CRUD；模型注册表；训练/部署编排（后续接 Docker API）；对话网关（后续转发 agent）；审计日志（后续）。
- **agent（Python 常驻，后续）**：LangGraph 对话编排；把已部署 LSTM 模型注册为 tool；调 serving 推理 API；记忆写 pgvector。
- **training（Python 动态容器，后续）**：拉数据集→预处理→训 PyTorch LSTM→评估→产物存 MinIO→回调 admin 注册版本。
- **serving（Python 按版本动态容器，后续）**：加载指定模型版本→暴露 `/predict`→注册到 admin endpoint。

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

**内置角色**：`ADMIN`（全部权限）、`OPERATOR`（全部权限）、`USER`（仅 `dataset:read`/`model:read`，面向对话）。
**初始账号**：`admin / admin123`（首次启动由 `DataInitializer` 写入，请上线前修改）。

## 5. 核心数据流（闭环）

数据集上传 → 落 MinIO + 元数据 PG → 后台发起训练（选数据集+超参）→ admin 起 training 容器 → 训练完注册**模型版本**(trained) → 部署某版本 → admin 起 serving 容器并注册 endpoint(deployed) → 终端用户对话提问 → front→admin(SSE)→agent→调 serving tool→返回预测 + 自然语言解读。

## 6. 动态部署机制（后续迭代）

admin 挂宿主 `/var/run/docker.sock`，用 Docker Java 客户端按 `ops-agent-training` / `ops-agent-serving` 镜像动态起容器，容器加入 compose bridge 网络；模型版本经 MinIO 注入。training 完成后回调 `/api/models` 注册，serving 启动后 admin 写 `serving_endpoints` 并暴露 `/api/serving/tools` 供 agent 发现。

## 7. 部署拓扑（docker-compose 规划）

常驻：`front / admin(挂 docker.sock) / agent / postgres(+pgvector) / minio`（redis 按需）。
`training`、`serving` 为模板镜像，由 admin 经 Docker API 在宿主机动态实例化，不预置在 compose 常驻列表。

## 8. 当前实现状态（基础版，本阶段已交付）

| 模块 | 状态 |
|------|------|
| Spring Boot 骨架 / JWT / 安全配置 / 全局异常 | ✅ 已实现 |
| RBAC：用户/角色/权限 CRUD + 授权 + 种子数据 | ✅ 已实现 |
| 数据集 Dataset 完整 CRUD | ✅ 已实现 |
| MinIO / pgvector / Docker 编排 | ⏳ 预留，未接入 |
| 模型/训练/部署 接口 | 🟡 接口桩（实体 JSON 收发） |
| 前端：登录 / 响应式布局 / 仪表盘 / 用户·角色·权限·数据集页 | ✅ 已实现（已 `npm run build` 通过） |
| 前端：对话工作台 / agent 接入 | ⏳ 后续 |

> 后端本机无 JDK，未编译验证；前端已用 Node 22 安装依赖并 `vite build` 通过。
