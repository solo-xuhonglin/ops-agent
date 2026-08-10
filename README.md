# ops-agent · 算法资产平台

> 将 LSTM 天气预测模型 **工具化 · 平台化 · Agent 化** 的后台管理系统：数据集 → 训练 → 模型版本 → serving 部署 → 预测，全链路闭环。
> **AI Agent（ops-agent-core）为内部运维/业务自动化助手**：监督训练任务、serving 健康、数据集/模型质量，异常时诊断并产出处置建议，人工确认后自动执行。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vuetify 4（管理后台 + 全局 Agent 助手浮窗） |
| 后端 | Spring Boot（唯一后端入口）+ gRPC（agent 双向流） |
| Agent | Python + LangGraph（决策图）+ ChatDeepSeek（deepseek-v4-flash，原生 function calling） |
| 算法 | Python + PyTorch（LSTM 时序预测） |
| 存储 | PostgreSQL（元数据）、MinIO（数据集/模型/日志三桶）、Redis（授权 key） |
| 部署 | Docker Compose（常驻服务 + 动态拉起的 training / serving 容器） |

## 目录结构

```
ops-agent/
├── README.md                  # 本文档（索引 + 定位）
├── docs/
│   ├── 01-architecture.md     # 项目架构
│   ├── 02-data-model.md       # 表结构
│   ├── 03-api.md              # API 设计
│   ├── 04-deploy.md           # 部署指南
│   └── 05-ops-scripts.md      # 运维脚本
├── ops-agent-front/           # Vuetify 4 前端（管理后台 + Agent 助手浮窗）
├── ops-agent-admin/           # Spring Boot 后端（总入口 / 编排 / gRPC server / 建议管理）
├── ops-agent-core/            # Python Agent worker（gRPC 出站拨号，零端口暴露）
├── ops-agent-data-train/      # Python：LSTM 训练容器（MinIO 输入 / 产物）
└── ops-agent-data-service/    # Python：LSTM 推理服务容器（FastAPI）
```

## 文档导航

- [项目架构](docs/01-architecture.md)
- [表结构](docs/02-data-model.md)
- [API 设计](docs/03-api.md)
- [部署指南](docs/04-deploy.md)
- [运维脚本](docs/05-ops-scripts.md)

## 状态

✅ **已闭环**：数据 → 训练 → 模型版本 → serving 部署 → 代理预测；**Agent 全链路**：gRPC 长连接（M1）→ LangGraph 决策 + 动态工具 schema（M2）→ 处置建议 + grantKey 授权闭环（M3）→ 前端助手浮窗（M4）→ Plan 多步规划 + 消息存储重构（M5）。

**Agent 安全模型**：任务级 scoped token 透传（LLM 只填业务参数，身份/授权头代码注入）；写操作「建议 → 人工确认 → 时效 grantKey（Redis，一次性）→ agent 持 key 执行」。

**消息存储架构**：Agent 端 MessageStore 按 LangGraph 轮次批量写入 ASSISTANT/TOOL_CALL/TOOL_RESULT；Java 端仅写入 USER（发消息时）和 APPROVAL（审批时），SSE 事件纯转发不落库。
