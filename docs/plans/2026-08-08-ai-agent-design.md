# AI Agent（业务流程辅助）设计（2026-08-08）

> 配套：brainstorming 流程产出；实现覆盖 `ops-agent-core`（新）/ `ops-agent-admin` / `ops-agent-front` / docker-compose / 部署脚本。
> 目标：在 `ops-agent-core` 开发 **Python AI Agent**，辅助现有业务流程（训练监督 / serving 健康 / 数据集质检 / 模型评估 / 状态问询）。Agent **不对外暴露任何接口**，通过 admin 的 **gRPC 双向流长连接**被远程调用；能力 = admin 现有 REST API（工具注册表存库，动态下发），写操作走「建议 + 人工确认 + 时效性 grantKey」闭环。
> 阶段定位：项目早期、测试/开发中，**代码以最新状态为准，不向前兼容**。

---

## 1. 关键决策（已与用户拍板）

| # | 议题 | 结论 |
|---|------|------|
| 1 | Agent 定位 | **内部运维/业务自动化助手**，不面向终端用户对话；辅助训练/ serving / 数据集 / 模型等业务流程 |
| 2 | 通信协议 | **gRPC 双向流（方案 C）**：admin 为 gRPC server（内网 `:9090`，不映射宿主），agent 为 client **出站拨号** —— agent **零监听端口**，物理上不暴露接口 |
| 3 | 多 agent 形态 | **一条流承载多个逻辑 agent**（worker 模型）：连接是传输层（一条），agent 是逻辑层（消息头 `workerId/agentId/taskId` 路由）；本期单 worker 单 agent（`ops-agent-core`），多 agent 为后续扩展 |
| 4 | LLM | **DeepSeek（OpenAI 兼容 API）**，用户提供 key，环境变量注入，不落代码 |
| 5 | 运行模式 | **纯被动响应（模式 2）**：agent 平时只保活长连接，admin 下发任务才行动；任务内自主决策。发现异常由 admin 现有 Poller 负责（复用 ServingHealthPoller / TrainingJobPoller） |
| 6 | 能力边界 | 工具集 = **admin 现有 REST API 子集**（只读 11 + 写 5），**不新开发能力接口**；工具清单存库（`agent_tools`），RegisterAck 动态下发 schema，agent 零硬编码 |
| 7 | 写操作授权 | **建议 + 人工确认**：agent 只产出处置建议 → 人工确认 → admin 签发**时效性 grantKey**（Redis，TTL 10min，GETDEL 原子消费）→ 推送给 agent → **agent 执行写工具**（agent 逻辑集中在 core，admin 侧重通信/鉴权/执行） |
| 8 | 鉴权模型 | ① 任务级 **scoped taskToken**（JWT，TTL 5min，权限裁剪）随 TaskDispatch 下发，agent 调只读工具时透传；② **身份标识由代码注入**（`X-Agent-Worker` 等），不靠"带不带 key"弱判断；③ **LLM 只填业务参数，系统参数（token/workerId/grantKey）由 http_client 代码层注入** |
| 9 | Agent 决策框架 | **轻量 function-calling 循环**（非 LangGraph）：LLM 返回 tool_call → 执行（HTTP 调 admin 现有 API）→ 回填 → 再问，直至收敛 |
| 10 | 前端形态 | **全局 AI 助手浮窗**（非独立页面）：右下角 FAB + 右侧抽屉，跨路由常驻；对话框样式；**处置建议在输入框上方弹出**；右上角历史图标切换「对话/历史」视图 |
| 11 | 部署 | docker-compose 新增常驻 `redis`（grantKey）+ `agent`（零端口）；admin 增 gRPC server；agent 唯一外呼 = DeepSeek API |

---

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────────┐
│ ops-agent-admin (Spring Boot, 唯一入口)                          │
│                                                                │
│ ┌─────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│ │ HTTP API    │  │ gRPC Server :9090 │  │ 能力执行层           │  │
│ │ (前端/用户)  │  │  AgentService     │  │ 现有 Service 直接调用 │  │
│ │ 管理面 API   │  │  WorkerRegistry   │  │ 写端点 + @RequireGrant│ │
│ │ (agent/*)   │  │  任务派发/事件回推  │  │ scoped token 兼容    │  │
│ └──────┬──────┘  └────────▲─────────┘  └───────▲────────────┘  │
│        │        bidi stream (agent 出站拨号)    │                │
│        │                  │                    │ HTTP (内网)     │
└────────┼──────────────────┼────────────────────┼────────────────┘
         │                  │                    │
         │        ┌─────────▼────────────────────▼───────┐
         │        │ ops-agent-core (Python, 零监听端口)    │
         │        │  gRPC client: 注册/收任务/回事件/收key  │
         │        │  agent: function-calling 循环          │
         │        │  tools/http_client: 调 admin 现有 API   │
         │        │  llm/deepseek: OpenAI 兼容调用          │
         │        └───────────────────────────────────────┘
         └─ 建议表 → 前端浮窗确认 → 签发 grantKey(Redis) → 推 agent
```

**数据流（处置闭环）**：
Poller 检测异常 → admin 派 `TaskDispatch`（带 scoped taskToken）→ agent 跑 function-calling 循环（调只读工具透传 token）→ `TaskResult`（结论 + suggestions）→ 落 `agent_suggestions`(PENDING) → 前端浮窗确认 → admin 签发 grantKey(Redis EX 600) + 沿流推 `AuthorizationGrant` → agent 调写工具（带 `X-Agent-Worker` + `X-Grant-Key`）→ admin 校验 + GETDEL 消费 → 执行 → 回执 → 状态 EXECUTED/FAILED。

---

## 3. 消息协议（proto/agent.proto，admin 与 core 共享）

```proto
syntax = "proto3";
package agent;

// 一条双向流承载 worker 内所有 agent（worker 模型）
rpc Connect(stream ClientMessage) returns (stream ServerMessage);

message Envelope {                 // 统一外包装
  string worker_id = 1;            // 连接级：worker 实例标识
  string agent_id = 2;             // 逻辑级：目标/来源 agent
  string task_id = 3;              // 任务关联
  oneof payload { ... }
}

// agent → admin                    // admin → agent
Register(worker_id, agents[])       RegisterAck(tools_schema[])   // 工具 schema 动态下发
TaskEvent(seq, type, content)       TaskDispatch(task_id, task_type, target, query, task_token)
TaskResult(conclusion, suggestions) AuthorizationGrant(action, target, grant_key, ttl)
AgentUpdate(agents[])               CancelTask(reason)
Pong                                Ping
```

**任务类型**（`TaskDispatch.task_type`）：

| task_type | target | agent 行为 |
|---|---|---|
| `diagnose_training` | trainingJobId | 拉状态/日志 → 诊断 → 建议（重试/中止） |
| `diagnose_serving` | endpointId | 健康/容器状态 → 诊断 → 建议（重启组合/下线） |
| `diagnose_dataset` | datasetId | 完整性/缺失/连续性 → 建议（补采/重新采集） |
| `model_review` | modelVersionId | 指标合理性/历史对比 → 建议（部署/回滚） |
| `question` | 自然语言 | agent 自主调工具回答 |

**TaskResult.suggestions[]**：`{action_type, target_type, target_id, params, reason, priority}`。
**写操作一律只产出建议**，落库后人工确认，agent 持 grantKey 执行。

---

## 4. 工具集（agent_tools 表种子数据，映射现有 REST API）

**只读工具**（透传 taskToken 即可调；权限点校验复用 RBAC）：

| 工具名 | 调用 | 现有端点 | 权限点 |
|---|---|---|---|
| `dataset.list` | GET | `/api/datasets` | dataset:read |
| `dataset.get` | GET | `/api/datasets/{datasetId}` | dataset:read |
| `dataset.get_file_url` | GET | `/api/datasets/{datasetId}/file/url` | dataset:read |
| `model.list` | GET | `/api/models` | model:read |
| `model.get` | GET | `/api/models/{modelVersionId}` | model:read |
| `training.list` | GET | `/api/training/jobs` | training:read |
| `training.get` | GET | `/api/training/jobs/{jobId}` | training:read |
| `training.get_logs_url` | GET | `/api/training/jobs/{jobId}/logs` | training:read |
| `serving.list` | GET | `/api/serving/endpoints` | serving:read |
| `serving.get` | GET | `/api/serving/endpoints/{endpointId}` | serving:read |
| `serving.predict` | POST | `/api/serving-proxy/{endpointId}/predict` | serving:read |

**写工具**（需人工确认 → grantKey）：

| 工具名 | 调用 | 现有端点 | 权限点 |
|---|---|---|---|
| `training.create` | POST | `/api/training/jobs` | training:write |
| `training.delete` | DELETE | `/api/training/jobs/{jobId}` | training:write |
| `serving.deploy` | POST | `/api/serving/deploy` | serving:write |
| `serving.undeploy` | POST | `/api/serving/endpoints/{endpointId}/undeploy` | serving:write |
| `dataset.collect_weather` | GET | `/api/datasets/{datasetId}/weather` | dataset:write |

**不暴露**：`/api/auth/*`、`/api/users|roles|permissions`（管理域）、`DELETE model|dataset|serving endpoint`（资产物理删除）、`POST/PUT dataset`、`/{id}/file`（数据录入）。
**语义澄清**：`training.delete`（DELETE job）= 中止/abort；`serving.restart` 不单列，agent 组合 `undeploy → deploy`（两步各需一次授权）；`serving.predict` 语义只读放只读层；预签名 URL host 为内网 `minio:9000`，agent 内网可直拉。

---

## 5. 安全与授权模型

**① 任务级 scoped taskToken**：
- admin 派发任务时签发：`{userId, permissions(裁剪), taskId, type=scoped, exp=5min}`（JWT，同 `JWT_SECRET`）；
- `JwtAuthenticationFilter` 兼容：`type=scoped` 时权限取 claims，不走用户表；
- agent 调只读工具透传 `Authorization: Bearer <taskToken>`；任务结束即失效，无法越权访问无关数据。

**② 强校验鉴权模型（写端点，无默认放行）**：

```
写端点鉴权：
├─ 请求带 X-Agent-Worker（代码注入的身份声明）→
│     ① taskToken 必须 type=scoped 且绑定该 worker 的有效任务
│     ② X-Grant-Key 必须存在且 Redis 校验(action+target 匹配) + GETDEL 消费
│     任一不满足 → 403
└─ 请求不带 X-Agent-Worker →
     必须用户 JWT(type=user) + @PreAuthorize 权限 → 放行（现有前端行为不变）
     scoped token 单独调用写端点 → 一律 403
```

- **身份 ≠ 授权**：`X-Agent-Worker` 只声明"这是 agent 调用"，能否执行取决于 grantKey；grantKey 只能由人工确认签发。
- **伪造无效**：编造 `X-Agent-Worker` 仍卡 scoped token + key 校验；scoped token 冒充用户被 type 判定拒绝。

**③ 系统参数注入（LLM 不可见不可改）**：
- `http_client.py` 每次调用自动附加：`Authorization`(taskToken)、`X-Agent-Worker`、`X-Agent-Task`、写工具时 `X-Grant-Key`（按 action 匹配 ctx）；
- LLM 的 tool_calls 参数**只有业务字段**；grantKey 不进 LLM 上下文；
- prompt injection 最多让 LLM 填错业务参数，碰不到鉴权链路。

---

## 6. Agent 内部结构（ops-agent-core）

```
ops-agent-core/
├── Dockerfile  requirements.txt        # grpcio / grpcio-tools / openai / pydantic
├── proto/agent.proto                   # 与 admin 共享
├── app/
│   ├── main.py                         # 入口：配置 → gRPC 拨号 → 注册 → 事件循环
│   ├── config.py                       # ADMIN_GRPC_ADDR / ADMIN_HTTP_BASE / DEEPSEEK_*
│   ├── transport/grpc_client.py        # 双向流：连接/指数退避重连/消息收发分发
│   ├── agent/core.py                   # function-calling 循环
│   ├── agent/context.py                # 任务上下文：taskId / taskToken / grantKeys
│   ├── tools/registry.py               # RegisterAck 下发的 schema 动态建工具表
│   ├── tools/http_client.py            # HTTP 调 admin 现有 API（系统参数注入）
│   ├── llm/deepseek.py                 # OpenAI 兼容封装（function calling）
│   └── events.py                       # TaskEvent 流式进度上报
└── tests/                              # pytest
```

**function-calling 循环**（收到 TaskDispatch 才跑）：
```
run_task(ctx, task):
  tools = registry.schemas()            # 来自 RegisterAck
  messages = [system, user(task.query)]
  loop:
    resp = llm.chat(messages, tools)
    if resp.tool_calls:
      for tc: emit(TaskEvent); result = http_client.execute(tc, ctx); messages.append(tool_result)
      continue
    conclusion, suggestions = parse(resp.content)
    emit(TaskResult, conclusion, suggestions); return
```

**关键行为**：写工具前置检查 ctx 是否有对应 grantKey —— 无则跳过执行，仅在建议说明"待确认"；有则带 key 执行。taskToken 存 ctx，任务结束即弃。

---

## 7. Admin 侧改动

1. **gRPC Server**（`net.devh:grpc-server-spring-boot-starter`，内网 `:9090`）：`AgentService.Connect(stream)` + `WorkerRegistry`（内存注册表，心跳 90s 超时清理）。
2. **`agent_tools` 表 + Repository + 种子数据**（16 个工具）；RegisterAck 下发 `enabled=true` 的 schema（OpenAI 格式）。
3. **Scoped taskToken**：`JwtUtil` 扩展签发；`JwtAuthenticationFilter` 兼容解析。
4. **Redis**：新增常驻服务（compose），admin 接 `spring-data-redis`（grantKey 存储/原子消费）。
5. **任务派发** `TaskDispatchService`：来源 = ①现有 Poller 检测异常自动派发 ②前端浮窗"让 Agent 分析"；任务超时 5min → `CancelTask`。
6. **写端点鉴权**：现有写 Controller 方法加 `@RequireGrant(action, target)`（拦截器实现第 5 节 ② 的双向判定）。
7. **建议闭环**：`approve` → 生成 grantKey → `Redis SET agent:grant:{key} EX 600` → 沿流推 `AuthorizationGrant`；`reject` → REJECTED；worker 离线时 approve 返回提示保持 PENDING。
8. **管理面 API**（人用，非 agent 能力接口，见第 8 节）。

---

## 8. 数据模型（新增 4 表）+ 管理面 API

```sql
CREATE TABLE agent_tools (
  id BIGSERIAL PRIMARY KEY, name VARCHAR(64) UNIQUE NOT NULL, description TEXT NOT NULL,
  http_method VARCHAR(8) NOT NULL, path_template VARCHAR(255) NOT NULL,
  auth_permission VARCHAR(64), is_write BOOLEAN DEFAULT FALSE,
  params_schema JSONB NOT NULL, enabled BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT now());

CREATE TABLE agent_tasks (
  id BIGSERIAL PRIMARY KEY, task_id VARCHAR(64) UNIQUE NOT NULL,
  task_type VARCHAR(32) NOT NULL, target_type VARCHAR(32), target_id BIGINT, query TEXT,
  status VARCHAR(16) DEFAULT 'DISPATCHED',  -- DISPATCHED/RUNNING/SUCCEEDED/FAILED/CANCELLED
  dispatched_by BIGINT, worker_id VARCHAR(64), conclusion TEXT,
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now());

CREATE TABLE agent_events (
  id BIGSERIAL PRIMARY KEY, task_id VARCHAR(64) NOT NULL, seq INT NOT NULL,
  event_type VARCHAR(16) NOT NULL,          -- progress/tool_call/error
  content TEXT, created_at TIMESTAMPTZ DEFAULT now());

CREATE TABLE agent_suggestions (
  id BIGSERIAL PRIMARY KEY, task_id VARCHAR(64),
  action_type VARCHAR(32) NOT NULL, target_type VARCHAR(32) NOT NULL, target_id BIGINT NOT NULL,
  params JSONB, reason TEXT, priority VARCHAR(8) DEFAULT 'NORMAL',
  status VARCHAR(16) DEFAULT 'PENDING',
  -- PENDING/APPROVED/REJECTED/EXECUTING/EXECUTED/FAILED/EXPIRED
  grant_key VARCHAR(64), confirmed_by BIGINT, confirmed_at TIMESTAMPTZ,
  executed_at TIMESTAMPTZ, result TEXT, created_at TIMESTAMPTZ DEFAULT now());
```

状态流转：
```
agent_tasks: DISPATCHED → RUNNING → SUCCEEDED / FAILED / CANCELLED
agent_suggestions:
  PENDING ─确认→ APPROVED ─agent 执行→ EXECUTING → EXECUTED / FAILED
      │          └─key 过期未执行→ EXPIRED
      └─忽略→ REJECTED
```

**管理面 API**（`agent:read` / `agent:write` 权限，RBAC 新增 agent 域；ADMIN/OPERATOR 全有，READONLY 仅 read）：

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/agent/suggestions` | agent:read | 建议列表（分页/状态筛选） |
| POST | `/api/agent/suggestions/{id}/approve` | agent:write | 确认：签发 grantKey + 推 AuthorizationGrant |
| POST | `/api/agent/suggestions/{id}/reject` | agent:write | 忽略 |
| GET | `/api/agent/tasks` | agent:read | 任务列表 |
| GET | `/api/agent/tasks/{taskId}` | agent:read | 任务详情（含 events） |
| POST | `/api/agent/tasks` | agent:write | 派发诊断/问询任务 |

---

## 9. 前端：全局 Agent 助手浮窗

```
App.vue
└── AgentAssistant.vue（全局挂载，跨路由常驻；v-if agent:read）
    ├── 右下角 FAB（机器人图标，未读建议红点）
    └── 右侧抽屉 v-navigation-drawer（聊天对话框样式，非模态）
        ├── 头部：标题 + 右上角 mdi-history 图标（对话/历史视图切换）
        ├── 主体：对话视图（TaskEvent/TaskResult 聊天气泡 + 工具调用折叠行）
        │        / 历史视图（历史任务 + 建议记录）
        ├── 输入框上方：PENDING 建议卡片滑入（action/target/reason/priority
        │              + [确认][忽略]，agent:write 才显示）
        └── 底部输入框（自然语言问询 → POST /api/agent/tasks）
```

- 抽屉状态存 Pinia（`agentStore`），路由切换不重置；
- 轮询 3s 拉取 PENDING 建议 + 当前任务事件；SSE 实时推送留后续增强；
- 训练/ serving / 数据集 / 模型详情页加"让 Agent 分析"按钮 → 派发对应 `diagnose_*` 任务；
- 确认/忽略走 `useConfirm().confirmDialog()`（项目既有约定）。

---

## 10. 部署与配置

**docker-compose 新增**：

```yaml
redis:
  image: redis:7-alpine
  container_name: ops-agent-redis
  restart: unless-stopped
  command: redis-server --appendonly yes
  networks: [opsnet]            # 不映射宿主端口
  healthcheck: { test: ["CMD","redis-cli","ping"], interval: 10s, timeout: 5s, retries: 5 }

agent:
  build: { context: ./ops-agent-core, dockerfile: Dockerfile }
  image: ops-agent-core:latest
  container_name: ops-agent-agent
  restart: unless-stopped
  depends_on: [admin, redis]
  environment:
    WORKER_ID: ops-agent-core-1
    ADMIN_GRPC_ADDR: admin:9090
    ADMIN_HTTP_BASE: http://admin:8080
    DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
    DEEPSEEK_BASE_URL: https://api.deepseek.com
    DEEPSEEK_MODEL: deepseek-chat
  networks: [opsnet]            # 零端口暴露
```

- admin：新增 gRPC `:9090`（内网不映射宿主）、依赖 `grpc-server-spring-boot-starter` / `spring-data-redis` / protobuf、env `AGENT_GRPC_PORT` / `REDIS_HOST` / `REDIS_PORT`；
- `deploy-remote.env` 新增 `DEEPSEEK_API_KEY`（用户提供，服务器 .env，不落代码）；
- agent 唯一外呼 = `api.deepseek.com`，服务器出网需放行；
- 镜像构建沿用项目约定：`deploy.sh` 服务器侧 `docker compose build agent` 后随 `up` 常驻（agent 非动态容器，不挂 docker.sock）。

---

## 11. 错误处理

| 故障 | 策略 |
|---|---|
| gRPC 断线 | agent 指数退避重连（1s→30s+抖动）→ 重连后重新 Register；admin 心跳 90s 超时清理 |
| 任务超时 5min | admin 发 CancelTask + agent asyncio.wait_for 双兜底 → CANCELLED |
| LLM 失败/限流 | agent 重试 2 次；仍失败 → TaskResult 带 error，任务 FAILED |
| 工具调用 4xx/5xx | 错误回填 LLM 上下文由其判断重试/换工具/放弃；写工具 key 消费失败视为无授权 → 跳过并说明 |
| grantKey 过期 | 建议置 EXPIRED；agent 无 key 不执行 |
| worker 离线时 approve | 返回"agent 离线"提示，建议保持 PENDING |

---

## 12. 测试与里程碑

**测试**：
- Python（pytest）：循环收敛（mock LLM）、schema 动态加载、http_client 头注入与业务参数映射、写工具无 key 跳过、proto 编解码；
- Java（集成）：gRPC 注册/下发/事件流闭环；grantKey 签发/消费/过期（testcontainers）；**写端点鉴权矩阵**（agent+key ✓ / agent 无 key ✗ / scoped 冒充用户 ✗ / 用户 JWT ✓）；scoped token 兼容现有 filter；
- E2E（ops-agent-test）：注册 → 派 question → 调只读工具 → 结论；确认建议 → 签发 key → 执行写工具 → 状态流转；
- 前端：浮窗开关/视图切换/建议卡片渲染。

**里程碑**（每期独立可验证）：
- **M1 通信层**：proto + admin gRPC server + agent 拨号/注册/心跳/重连；任务下发→事件→结果闭环（固定结论，不接 LLM）；
- **M2 Agent 决策**：DeepSeek + function-calling 循环 + schema 动态下发 + 只读工具调用（taskToken 透传）；
- **M3 建议闭环**：建议表 + approve/reject + grantKey(Redis) + AuthorizationGrant + 写工具执行 + 状态流转；
- **M4 前端浮窗**：AgentAssistant 全局组件 + 建议卡片 + 历史视图 + 详情页"分析"按钮；
- **M5 联调部署**：compose 加 redis/agent + E2E + 文档更新（01-architecture / 03-api / 04-deploy / README）。

---

## 13. 待办/后续扩展

- 多 agent 拆分（按域注册多个 agent_id，消息头路由已预留）；
- SSE 事件实时推送（前端）；
- `training.retry` / `mark_status` 等写工具（需 admin 新增端点，本期不做）；
- agent 记忆（pgvector，原规划中"记忆"能力，本期无状态任务型暂不需要）。
