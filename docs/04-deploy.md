# ops-agent 部署指南

> 配套部署文件已生成在仓库根目录：`docker-compose.yml`、`deploy.sh`、`.env.example`；前端 `ops-agent-front/Dockerfile`(仅拷贝 `dist`) + `nginx.conf`；后端 `Dockerfile`(仅拷贝 `target/*.jar`)。
>
> **策略：服务器上拉取代码 → 宿主机编译 → Docker 仅拷贝 build 产物**。依赖安装与编译在宿主机命令行完成，可天然缓存：Maven 本地仓库固定为项目内 `$PROJECT_DIR/.m2`，产物在 `target/*.jar`（标准位置，已被 `.gitignore` 忽略）；Docker 镜像只 `COPY` 成品，构建秒级，本地不上传任何包。

## 部署拓扑

```
用户浏览器
   │  http://<IP>           (80)
   ▼
[ front: nginx ] ── /api/ 反向代理 ──► [ admin: Spring Boot ] (8080)  ◄── gRPC :9090（内网）
                                          │  JDBC   │  docker.sock（动态起容器）
                                          ▼         ▼
                                    [ postgres ]  [ train: ops-agent-train ]（按任务动态起）
                                    [ redis   ]  [ serving: ops-agent-serving ]（按版本动态起）
                                          │  S3    │  gRPC 双向流（agent 出站拨号，零端口）
                                          ▼       ▼
                                    [ minio: datasets/models/logs 三桶 ]  [ agent: ops-agent-core ]
```
> admin 挂载宿主 `/var/run/docker.sock`：点击训练时经 docker-java 拉起 `ops-agent-train` 容器（训练产物回传 MinIO，跑完即回收）；部署模型时拉起 `ops-agent-serving` 常驻容器（只加入内网 `opsnet`、不映射宿主端口），外部经 admin 的 `/api/serving-proxy/{endpointId}/predict` 代理调用推理。
> **agent（Python）** 为常驻 worker：作为 gRPC client **出站拨号** admin 的 gRPC server（内网 `:9090`，不映射宿主），**零监听端口**；agent 与 redis 均不对外暴露。agent 唯一外呼：`api.deepseek.com`（LLM）。

## 一、服务器前置要求

> 目标系统：**TencentOS Server 4 for x86_64**（RHEL 9 系，yum/dnf 包管理）。
> 所有镜像（`node:22`、`eclipse-temurin:17`、`nginx`、`pgvector/pgvector:pg17`）均为 `linux/amd64`，与 x86_64 匹配，无需指定平台。

1. 已安装 Docker Engine 与 Docker Compose v2（`docker compose version` 可查）。
   - TencentOS Server 4（RHEL 系）安装方式：
     ```bash
     # 方式 A：腾讯云官方扩展源（推荐，自带 compose 插件）
     dnf install -y docker docker-compose-plugin
     systemctl enable --now docker

     # 方式 B：Docker 官方 repo（若方式 A 无包）
     dnf install -y yum-utils
     yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
     dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
     systemctl enable --now docker
     ```
   - 注意：TencentOS 默认可能带 `podman`，与 docker 命令冲突时卸掉 podman 或改用 `docker` 命令即可。
2. 安全组放行端口：`80`（前端）、`8080`（后端，按需）、`5432`（数据库，建议仅内网/VPC 放行，公网勿开）。
3. 服务器需能访问 git 仓库（SSH key 或 HTTPS 凭证）与 Docker Hub（拉取基础镜像）。
4. **无需本地上传任何包**：`dist/`、`target/` 均在服务器内由 Docker 多阶段构建生成。

## 二、修改密钥（上线前必做）

**权威环境文件在 `/root/ops-agent.env`**（`deploy.sh` 的 `ENV_FILE` 默认值；首次部署无此文件时会从 `.env.example` 初始化后退出，填完密钥重跑）。编辑 `/root/ops-agent.env`，至少替换：

- `DB_PASSWORD` —— 强密码
- `JWT_SECRET` —— 至少 32 位随机串，可用 `openssl rand -base64 48` 生成
- `SERVER_IP` / `CORS_ALLOWED_ORIGINS` —— 改成你的服务器公网 IP
- `DEEPSEEK_API_KEY` —— Agent 的 LLM key（DeepSeek，`https://api.deepseek.com`，model `deepseek-v4-flash`，原生 thinking + function calling）；缺失时 agent 工具调用会失败

> 演示账号由后端 `DataInitializer` 写入：`admin / admin123`（管理员，全部权限）、`user / user123`（运营人员，业务读写）。本系统为演示用途，无需修改默认密码。

## 三、部署（全程服务器内：拉取 → 编译 → 构建 → 启动）

```bash
# REPO_URL 默认值已是 https://github.com/solo-xuhonglin/ops-agent.git，
# 仅在需要换仓库/目录/分支时用环境变量覆盖：
# export REPO_URL="https://github.com/solo-xuhonglin/ops-agent.git"
# export PROJECT_DIR="/opt/ops-agent"
# export BRANCH="main"

# 首次运行会自动 git clone；之后运行会 git pull 更新
chmod +x deploy.sh
./deploy.sh
```

脚本行为：
1. 检查 Docker / Compose；
2. 首次 `git clone` 仓库到 `PROJECT_DIR`，后续 `git pull --ff-only` 更新代码；
3. 若无 `.env` 则从 `.env.example` 复制并退出，提示你填密钥后重跑；
4. **宿主机编译**：前端 `npm install && npm run build`（产物 `ops-agent-front/dist`，依赖缓存于宿主机 `node_modules`）；后端 `mvn clean package -Dmaven.repo.local=$PROJECT_DIR/.m2`（产物在 `target/*.jar`，依赖缓存于项目内 `.m2`）；Dockerfile 直接 `COPY target/*.jar`；
5. **按需**预构建工具镜像（`profiles:["tools"]`，不随 `up` 启动，仅供 admin 经 docker-java 动态实例化）：
   - 训练镜像 `ops-agent-train:latest`（`ops-agent-data-train/`，基于 `python:3.11-slim` 自装 CPU 版 torch）；
   - 推理镜像 `ops-agent-serving:latest`（`ops-agent-data-service/`，FastAPI + CPU 版 torch）。详见下方「工具镜像的构建时机」；
6. `docker compose up -d --build` —— 镜像仅 `COPY` 上述成品，**构建秒级**，本地不上传任何包。

### 按服务部署（避免每次全量重编）

`./deploy.sh` 不带参数等同 `./deploy.sh all`（上述完整流程）。日常只改了某一端时，可只部署对应服务，跳过其余编译环节：

```bash
./deploy.sh --help              # 查看完整用法
./deploy.sh admin               # 只跑 mvn package → 重建 admin 镜像 → 重启
./deploy.sh front               # 只跑 npm build → 重建 front 镜像 → 重启
./deploy.sh front admin         # 前后端都更新，不动工具镜像
./deploy.sh --build-only train  # 只构建训练镜像，不 up
./deploy.sh --build-only serving # 只构建推理镜像，不 up
./deploy.sh infra               # 只拉起 postgres + minio + minio-init
```

**可选服务**

| 服务 | 含义 | 编译动作 | 是否启动容器 |
|------|------|----------|--------------|
| `all` | 全部（默认） | 前端 + 后端 + 训练/推理镜像 | 是（全部） |
| `admin` | 后端 Spring Boot | `mvn package` | 是 |
| `front` | 前端 Vue | `npm build` | 是 |
| `train` | 训练镜像 | 按哈希判定是否构建 | **否**（`profiles:[tools]`，只构建） |
| `serving` | 推理镜像 | 按哈希判定是否构建 | **否**（`profiles:[tools]`，只构建） |
| `infra` | 展开为 `postgres` `minio` `minio-init` | 无 | 是 |
| `postgres` / `minio` | 单独指定基础设施 | 无 | 是 |
| `agent` | Agent（Python worker，gRPC 出站拨号） | 服务器 `docker compose build agent`（pip 走清华镜像） | 是（零端口） |
| `redis` | Redis（grantKey 存储） | 无（拉取 `redis:7-alpine`） | 是（零端口） |

`backend` / `frontend` 作为 `admin` / `front` 的别名同样可用。

**可选开关**

| 选项 | 作用 |
|------|------|
| `--no-pull` | 跳过 `git pull`，用服务器当前工作树的代码构建 |
| `--build-only` | 只编译 / 构建镜像，不执行 `compose up` |
| `--no-build` | 跳过编译与镜像构建，仅重启容器 |
| `--no-deps` | `compose up` 时不连带启动依赖服务（如只重启 admin 不触碰 postgres） |
| `--force-train` | 强制重建训练镜像（等价 `FORCE_BUILD_TRAIN=1`） |
| `--force-serving` | 强制重建推理镜像（等价 `FORCE_BUILD_SERVING=1`） |

> ℹ️ **依赖会被连带启动**：compose 的 `depends_on` 决定了 `./deploy.sh front` 也会把 `admin` 纳入启动图（`front` 依赖 `admin`），
> 因而可能顺带重启后端。这是为了保证依赖确实在跑。若要严格只动目标服务，加 `--no-deps`：
> ```bash
> ./deploy.sh --no-deps front          # 重建前端但不碰 admin
> ./deploy.sh --no-build --no-deps front   # 仅重启前端容器
> ```
> 依赖链：`front → admin → postgres, minio, redis`；`agent → admin, redis`。

> ⚠️ **改动了 `deploy.sh` 自身的提交**：bash 是边读边执行的，运行中脚本被 `git pull` 覆盖会产生难以排查的怪问题。
> 正确做法是先单独 `git pull`，再用 `--no-pull` 运行：
> ```bash
> git -C /opt/ops-agent pull --ff-only origin main
> ./deploy.sh --no-pull admin
> ```

> 仅重启某个容器（不重新编译、不影响依赖）：
> ```bash
> ./deploy.sh --no-build --no-deps admin
> ```

### 工具镜像的构建时机

训练镜像约 1.9GB、推理镜像约 800MB（均含 CPU 版 torch），**不是每次部署都重建**。`deploy.sh` 对两者都用「镜像是否存在 + 构建上下文内容哈希」判定：

```
训练镜像  sha256(Dockerfile + requirements.txt + train.py)  与 .deploy-cache/train-image.sha256   比对
推理镜像  sha256(Dockerfile + requirements.txt + serve.py)  与 .deploy-cache/serving-image.sha256 比对
```

| 场景 | 行为 | 耗时 |
|------|------|------|
| 首次部署 | 完整构建：拉基础镜像 + 装 torch 及依赖 | 约 3–6 分钟 |
| 构建上下文均未变 | **跳过构建**，脚本直接进入下一步 | 0 秒 |
| 只改了 `train.py` / `serve.py` | 重建，但 `pip install` 层命中 Docker 缓存，仅重跑末尾 `COPY` | 数秒 |
| 改了 `requirements.txt` / `Dockerfile` | 重建且依赖层缓存失效，重新安装 | 约 3–6 分钟 |

两个 Dockerfile 都刻意把 `COPY *.py` 放在 `pip install` **之后**，正是为了让代码日常改动不触发依赖重装。

强制重建（例如想刷新基础镜像）：

```bash
./deploy.sh --force-train              # 全量部署 + 强制重建训练镜像
./deploy.sh --force-train train        # 只强制重建训练镜像
./deploy.sh --force-serving serving    # 只强制重建推理镜像
FORCE_BUILD_TRAIN=1 ./deploy.sh        # 等价的环境变量写法
FORCE_BUILD_SERVING=1 ./deploy.sh
```

## 四、验证

```bash
docker compose ps                      # 三个服务均 healthy/running
curl http://localhost/api/actuator/health   # 后端健康检查（若已开启 actuator）
# 浏览器打开 http://<服务器IP> 应看到登录页
```

## 五、常见问题

| 现象 | 排查 |
|------|------|
| 前端白屏 / 502 | `docker compose logs front` 看 nginx；确认 admin 已起 |
| 后端连不上库 | `docker compose logs admin`；确认 postgres healthy、密码一致 |
| CORS 报错 | 检查 `.env` 中 `CORS_ALLOWED_ORIGINS` 含浏览器实际访问地址 |
| npm 安装慢/超时 | 依赖在宿主机 `node_modules` 缓存，首次慢后续快；服务器需能访问 npm registry |
| maven 依赖下载慢 | 依赖在项目内 `$PROJECT_DIR/.m2` 缓存，首次慢后续快；服务器需能访问 Maven Central |
| 拉取 Docker 基础镜像慢 | 可给 Docker 配镜像加速：编辑 `/etc/docker/daemon.json` 加 `registry-mirrors` 后 `systemctl restart docker` |
| pgvector 函数缺失 | 后端 `DataInitializer` 启动时会自动 `CREATE EXTENSION IF NOT EXISTS vector;`；若仍缺失，手动连库执行该语句（需超级用户） |

## 六、后续迭代

- 训练编排已落地：`admin` 已挂载宿主 `docker.sock`，`docker-compose.yml` 含 `train` / `serving` 服务（均 `profiles:["tools"]`，`deploy.sh` 会按需预构建镜像）。serving 推理部署也已落地：模型 READY 后可在前端「模型管理」页点击部署，经 admin 动态拉起 `ops-agent-serving` 容器（仅内网），「模型服务」页可测试推理与下线。
- 生产建议加 HTTPS（在 `front` 前再挂一层 `nginx-proxy` + `acme-companion` 或自建 cert）。

## 七、存储约定

业务数据统一落在 MinIO，PG 仅保留元数据：

| 用途 | MinIO 路径 |
| --- | --- |
| 数据集（采集 / 上传的文件） | `datasets/<datasetId>/...` |
| 训练产物（模型权重、指标） | `models/<modelVersionId>/model.pt`、`metrics.json` |
| 训练日志 | `artifacts/<jobId>/logs.txt` |
