# 模型管理 & 训练流水线设计（2026-08-08）

> 配套：brainstorming 流程产出；实现覆盖 `ops-agent-admin` / `ops-agent-front` / `ops-agent-data-train` / 部署脚本。
> 目标：新增「模型管理」界面；「数据集」界面增加「数据条数」与「训练」入口；点击训练时 admin 起一个独立 docker 容器跑训练，训练代码位于 `ops-agent-data-train`，产物上传 MinIO。
> 阶段定位：项目早期、测试/开发中，**代码以最新状态为准，不向前兼容**。

---

## 1. 关键决策（已与用户拍板）

| # | 议题 | 结论 |
|---|------|------|
| 1 | 训练目标 | 气象时序预测（LSTM），与气象数据集语义、`ModelVersion.algorithm` 默认 `LSTM`、前端 ECharts 气温图对齐 |
| 2 | 拉容器方式 | admin 挂载宿主 `docker.sock`，用 **docker-java** 动态 `createContainer`/`start`，一任务一容器，用完销毁 |
| 3 | 数据供给 | 数据集采集/上传时已落 MinIO，训练容器**直接读 MinIO**（不直连 PG、不做服务间回调），输入有快照、可复现 |
| 4 | 状态回写 | admin 侧 `@Scheduled` **轮询容器状态**（容器保持"哑"，不出网回 admin，无需服务间鉴权） |
| 5 | 界面范围 | 模型列表 + 训练任务列表（**不做**部署上线 serving） |
| 6 | 镜像构建 | compose `profiles:["tools"]` 定义 `train` 服务；`deploy.sh` 执行 `docker compose --profile tools build train` 预构建 `ops-agent-train:latest` |
| 7 | 并发 | 暂不限（同数据集可同时多个 RUNNING，后续再加限制） |
| 8 | 基础镜像 | **CPU 版** pytorch（`pytorch/pytorch:2.x-cpu`），无需 GPU 环境即可跑 |
| 9 | 模型详情 | 仅**标量指标卡片**（MAE/RMSE/Loss/epochs），不画 loss 曲线 |
| 10 | 遗留清理 | 气象数据不再入 PG，全部落 MinIO；对应实体/Repository/引用一并删除，不留兼容 |

---

## 2. 数据存储模型修正 + 遗留清理

目标状态：**PG `datasets` 表只存元数据，实际数据一律落 MinIO**。

- **`WeatherService.collect()` 改造**：采集 Open-Meteo 后不再逐行写 PG，改为在内存拼成 CSV（列 `region,time,temperature,precipitation`），上传到 `datasets/<id>/weather.csv`，返回行数。
- **`DatasetService.create/update`**：采集/处理完成后把**真实 `objectKey`**（= `<datasetId>/weather.csv`）与 `rowCount` 回写 `datasets` 表，替换现在那个假的 `weather://<name>` 占位值。
- **`POST /api/datasets/{id}/file`（手动上传）**：上传后顺带数一遍行数写回 `rowCount`，与采集路径行为一致。无论数据集是"采集来的"还是"传上来的"，`rowCount` 都可信。
- **删除遗留**：气象明细的 PG 实体与 Repository，及其在 `DatasetService`/`WeatherService` 中的引用。
- **`GET /api/datasets/{id}/weather` 端点保留**：内部改从 MinIO 下载 CSV 解析，返回结构完全不变（`regions/times/series`）。前端那张 ECharts 气温/降水图**零改动**（代价：每次看图都下载解析一次 CSV，数据量大时后续可加缓存）。

---

## 3. 后端训练链路（`ops-agent-admin`）

### 3.1 依赖与编排
- `pom.xml` 引入 `docker-java-core` + `docker-java-transport-httpclient5`（3.4.x）。
- `docker-compose.yml`：取消 admin 的 `docker.sock` 挂载注释；新增 `train` 服务定义，`profiles:["tools"]` 使其不随 `up` 启动，仅用于构建出 `ops-agent-train:latest`；`deploy.sh` 加对应 build 步骤。

### 3.2 触发
- `POST /api/training/jobs`，body：`{datasetId, name, version, algorithm, hyperparameters{seqLen,hiddenSize,epochs,batchSize,lr}}`。
- 校验数据集存在且 `objectKey` 非空 → 建 `ModelVersion(status=TRAINING)` 与 `TrainingJob(status=PENDING)` → 交给 `TrainingLauncher` → **立即返回 job**，不阻塞。

### 3.3 `TrainingLauncher`
- `createContainer` 起 `ops-agent-train:latest`，容器名 `ops-agent-train-job-<jobId>`，加入 compose bridge 网络（容器内可用 `http://minio:9000`）。
- 全部输入走环境变量（见 §4）：MinIO 连接信息、`DATASET_OBJECT_KEY`、`MODEL_VERSION_ID`、`JOB_ID`、超参。
- `autoRemove=false`——必须留着容器才能读 exit code 和日志，读完再删。

### 3.4 `TrainingJobPoller`
- `@Scheduled(fixedDelay = 5s)` 扫描 `PENDING/RUNNING` 的 job：
  - 容器 `exited` 且 `exitCode==0` → 从 MinIO 读 `models/<mvId>/metrics.json`，回填 `ModelVersion.metrics`、`artifactKey`，状态置 `READY`，job 置 `SUCCEEDED`；
  - 否则 job 置 `FAILED`，模型置 `FAILED`；
  - **无论成败**：`docker logs` 拉全量输出传 `artifacts/<jobId>/logs.txt`，写 `job.logKey`，然后销毁容器；
  - 超过 `train.timeout-minutes` 强杀并标 `FAILED`。
- 新增 `GET /api/training/jobs/{id}/logs` 返回日志预签名 URL。
- 配置项：`train.enabled` / `train.image` / `train.network` / `train.timeout-minutes`。

---

## 4. 训练模块 `ops-agent-data-train`

**技术栈**：Python 3.11 + PyTorch（CPU 版）。容器不挂端口、不回连 admin。

**文件**：
- `Dockerfile`：`FROM pytorch/pytorch:2.x-cpu`，`CMD ["python","train.py"]`（先一把梭单文件，必要时拆 `model.py`/`dataset.py`）。
- `requirements.txt`：`torch, pandas, numpy, boto3, scikit-learn`（boto3 对接 MinIO 的 S3 兼容 API）。
- `train.py`：按 §4 流程图 7 步走，全部配置从环境变量读。

**MinIO 路径约定**（bucket 名 = `datasets`）：
- 输入：`datasets/<datasetId>/weather.csv`
- 模型：`models/<modelVersionId>/model.pt`
- 指标：`models/<modelVersionId>/metrics.json`
- 日志：直接 `print` 到 stdout，由 admin 经 `docker logs` 抓取

**环境变量清单**（admin 起容器时注入）：
```
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=****
MINIO_SECRET_KEY=****
MINIO_BUCKET=datasets
DATASET_OBJECT_KEY=datasets/<datasetId>/weather.csv
MODEL_VERSION_ID=<mvId>
JOB_ID=<jobId>
SEQ_LEN=24   HIDDEN_SIZE=64   EPOCHS=50   BATCH_SIZE=32   LR=0.001
```

**`metrics.json` schema**：
```json
{
  "mae": 0.0, "rmse": 0.0, "train_loss": 0.0,
  "epochs": 50, "hidden_size": 64, "seq_len": 24
}
```

**训练流程**（容器内部）：读环境变量 → 从 MinIO 下载数据集 CSV → 滑窗（SEQ_LEN 回看窗口 → 预测下一步）构造样本 → PyTorch LSTM 训练（EPOCHS 轮前向/反向传播）→ 评估产出 MAE/RMSE/Loss → 上传 `model.pt` + `metrics.json` 到 MinIO → 日志打印 stdout。

---

## 5. 前端三处改动（`ops-agent-front`，Vue 3 + Vuetify 3 + Vite）

API 统一走 `/api` 的 axios 实例；路由用 `meta.perm` 做权限门禁，侧边栏 `menus` 按 `auth.hasPerm` 过滤。

### 5.1 数据集页（`DatasetList.vue`）
- `headers` 新增「数据条数」列，绑定 `item.rowCount`，千分位格式化，`—` 表示空。后端 `Dataset.rowCount` 已存在，纯展示。
- `actions` 列新增「训练」按钮（`mdi-rocket-launch`，受 `training:write` 控制）：弹出**训练对话框**——数据集自动带出（只读），填模型名称、版本号（默认 `v1`）、算法（下拉锁定 `LSTM`）；超参区 `seqLen/hiddenSize/epochs/batchSize/lr` 带默认值；确认 → `POST /api/training/jobs` → toast「已提交，可在训练任务页查看进度」+ 一键跳转训练任务页。

### 5.2 模型管理页（`views/models/ModelList.vue`，perm `model:read`）
- 表格列：名称 / 版本 / 算法 / 关联数据集 / 状态（训练中·就绪·失败 chip）/ 关键指标（MAE·RMSE）/ 创建时间 / 操作。
- 操作：`详情`（对话框展示标量指标卡片）、`下载`（拿 `GET /api/models/{id}/download` 预签名 URL 直接下载 `model.pt`）、`删除`。
- 路由 `/models`，侧边栏加「模型管理」（`mdi-cube-outline`）。

### 5.3 训练任务页（`views/training/TrainingJobList.vue`，perm `training:read`）
- 表格列：任务名 / 数据集 / 模型版本 / 状态（PENDING·RUNNING·SUCCEEDED·FAILED chip）/ 开始·结束时间 / 操作。
- 操作：`查看日志`（拿 `GET /api/training/jobs/{id}/logs` 预签名 URL，对话框内 `<pre>` + 自动滚底展示）。
- **自动刷新**：页面上只要有任务处于 `PENDING/RUNNING`，就 `setInterval` 每 5s 拉一次列表；全部终态后停轮询，组件卸载清定时器。

### 5.4 路由 + 布局
- `router.js` 加两条 children 路由（带 `perm`）。
- `AdminLayout.vue` 的 `menus` 数组加两个菜单项。

---

## 6. MinIO 存储约定 & REST 契约

**Bucket**：`datasets`（来自 `minio.bucket`，`minio-init` 已自动建好）。对象 key 均为该 bucket 内前缀：

| 用途 | 对象 key |
|------|----------|
| 数据集输入文件 | `datasets/<datasetId>/weather.csv` |
| 模型文件 | `models/<modelVersionId>/model.pt` |
| 指标文件 | `models/<modelVersionId>/metrics.json` |
| 训练日志 | `artifacts/<jobId>/logs.txt` |

> 老代码 `Dataset.objectKey` 写的是 `datasets/<id>/<file>`（把 bucket 名塞进 key），新设计统一去掉前缀、只留 `<datasetId>/weather.csv`，避免 `datasets/datasets/...` 套娃。

**REST 接口**（基于现有骨架 Controller 扩展，权限注解沿用 `model:*` / `training:*`）：

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/training/jobs` | `training:write` | 触发训练（新 DTO：datasetId/name/version/algorithm/hyperparameters）→ 立即返回 job |
| GET | `/api/training/jobs` | `training:read` | 列表（分页 `data.data.content`/`totalElements`） |
| GET | `/api/training/jobs/{id}` | `training:read` | 详情 |
| GET | `/api/training/jobs/{id}/logs` | `training:read` | 返回 `artifacts/<jobId>/logs.txt` 预签名 URL |
| DELETE | `/api/training/jobs/{id}` | `training:write` | （可选）停容器+删记录 |
| GET | `/api/models` | `model:read` | 模型列表 |
| GET | `/api/models/{id}` | `model:read` | 模型详情 |
| GET | `/api/models/{id}/download` | `model:read` | 返回 `models/<id>/model.pt` 预签名 URL |
| DELETE | `/api/models/{id}` | `model:write` | 删除模型 |
| GET | `/api/datasets/{id}/weather` | `dataset:read` | 保留；改从 MinIO 解析 CSV，返回结构不变 |

---

## 7. 部署

- `docker-compose.yml`：取消 admin 的 `docker.sock` 挂载注释；新增 `train` 服务（`profiles:["tools"]`，`build: ./ops-agent-data-train`，产出 `ops-agent-train:latest`）。
- `deploy.sh`：加 `docker compose --profile tools build train` 预构建镜像。
- `docs/04-deploy.md`：更新部署拓扑，补「存储约定」小节（MinIO 三类路径）。

---

## 8. 文件改动清单

**后端 `ops-agent-admin`**
- 新增：`config/TrainProperties.java`、`service/TrainingLauncher.java`、`service/TrainingJobPoller.java`、`dto/TrainingRequest.java`
- 改：`controller/TrainingController.java`（新 create 逻辑 + `/logs`）、`controller/ModelController.java`（+`/download`）、`service/TrainingJobService.java`、`service/ModelVersionService.java`、`service/WeatherService.java`（collect 改 MinIO CSV + rowCount）、`service/DatasetService.java`（上传补 rowCount、写真实 objectKey）、`controller/DatasetController.java`（`/weather` 改 MinIO 解析）、`pom.xml`（docker-java）、`src/main/resources/application.yml`（`train.*`）
- 删：气象明细实体 + 其 Repository + 相关引用

**前端 `ops-agent-front`**
- 改：`views/datasets/DatasetList.vue`、`plugins/router.js`、`layouts/AdminLayout.vue`
- 新增：`views/models/ModelList.vue`、`views/training/TrainingJobList.vue`

**训练模块 `ops-agent-data-train`**
- 新增：`Dockerfile`、`requirements.txt`、`train.py`（可选 `model.py`/`dataset.py`）

**部署**
- 改：`docker-compose.yml`、`deploy.sh`、`docs/04-deploy.md`

---

## 9. 与 `docs/01-architecture.md` 的差异说明

架构文档第 6 节写的是"训练容器完成后**回调** `/api/models` 注册版本"。本方案改为 **admin 轮询容器状态**回填（决策 #4）——容器不出网、无服务间 token 鉴权、崩溃也能靠轮询兜底，更符合"容器保持哑"的简化目标。后续若需文档与实现一致，更新 `01-architecture.md` 第 6 节措辞即可。

---

## 10. 后续可扩展（非本期）

- 同数据集并发训练限制；serving 部署上线（对接 `ServingEndpoint`，再起 serving 容器）；模型详情 loss 曲线图；GPU 基础镜像；`/weather` 解析结果缓存。
