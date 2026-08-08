# ops-agent · 算法资产平台（LSTM 天气模型 Agent 化）

> 将 LSTM 天气预测模型 **工具化 · Agent 化 · 平台化** 的后台管理系统。
> 终端用户用自然语言对话，agent 把「已部署的 LSTM 模型」当作工具调用来生成天气预测与分析。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + Vuetify 4（响应式布局） |
| 后端 | Spring Boot（唯一后端入口） |
| Agent | Python + LangGraph（对话编排 + 工具调用） |
| 算法 | Python + PyTorch（LSTM 时序预测） |
| 存储 | PostgreSQL + pgvector（元数据 / 记忆）、MinIO（S3 兼容对象存储） |
| 部署 | Docker Compose（常驻服务 + 动态拉起的 training / serving 容器） |

## 目录结构

```
ops-agent/
├── README.md                  # 本文档（索引 + 定位）
├── docs/
│   ├── 01-architecture.md     # 项目架构
│   ├── 02-data-model.md       # 表结构
│   ├── 03-api.md              # API 设计
│   └── 04-deploy.md           # 部署指南
├── ops-agent-front/           # Vuetify 4 前端（管理后台 + 对话工作台）
├── ops-agent-admin/           # Spring Boot 后端（总入口 / 编排 / Docker 生命周期）
├── ops-agent-core/            # Python：agent / training / serving（后续）
├── ops-agent-data-train/      # Python：LSTM 训练容器（MinIO 输入 / 产物）
└── ops-agent-data-service/    # Python：LSTM 推理服务容器（FastAPI）
```

## 文档导航

- [项目架构](docs/01-architecture.md)
- [表结构](docs/02-data-model.md)
- [API 设计](docs/03-api.md)
- [部署指南](docs/04-deploy.md)

## 状态

🔧 **开发中** —— 数据 → 训练 → 模型版本 → 部署 serving → 代理预测链路已闭环；agent（LangGraph）对话编排为后续项。
