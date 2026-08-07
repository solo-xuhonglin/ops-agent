# ops-agent 部署指南

> 配套部署文件已生成在仓库根目录：`docker-compose.yml`、`deploy.sh`、`.env.example`；前端 `ops-agent-front/Dockerfile`(多阶段，镜像内 npm build) + `nginx.conf`；后端复用原有 `Dockerfile`(多阶段，镜像内 maven package)。
>
> **策略：全程在服务器上拉取 + 编译 + 构建**，本地不上传任何 `dist/` 或 `target/` 产物。前端 `node:22` 镜像内 `npm install && build`，后端 `maven` 镜像内 `package`，均不依赖本地构建。

## 部署拓扑

```
用户浏览器
   │  http://<IP>           (80)
   ▼
[ front: nginx ] ── /api/ 反向代理 ──► [ admin: Spring Boot ] (8080)
                                          │  JDBC
                                          ▼
                                    [ postgres + pgvector ] (5432)
```

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

编辑 `.env`，至少替换：

- `DB_PASSWORD` —— 强密码
- `JWT_SECRET` —— 至少 32 位随机串，可用 `openssl rand -base64 48` 生成
- `SERVER_IP` / `CORS_ALLOWED_ORIGINS` —— 改成你的服务器公网 IP

> 默认账号 `admin / admin123` 由后端 `DataInitializer` 写入，上线后务必修改（见 `./01-architecture.md` 第 4 节）。

## 三、部署（全程服务器内：拉取 → 编译 → 构建 → 启动）

```bash
# 上传 repo 地址 / 目录 / 分支可用环境变量覆盖，或直接改 deploy.sh 顶部默认值
export REPO_URL="你的git仓库地址"
export PROJECT_DIR="/opt/ops-agent"
export BRANCH="main"

# 首次运行会自动 git clone；之后运行会 git pull 更新
chmod +x deploy.sh
./deploy.sh
```

脚本行为：
1. 检查 Docker / Compose；
2. 首次 `git clone` 仓库到 `PROJECT_DIR`，后续 `git pull --ff-only` 更新代码；
3. 若无 `.env` 则从 `.env.example` 复制并退出，提示你填密钥后重跑；
4. `docker compose up -d --build` —— **前端在 `node:22` 镜像内 `npm install && build`，后端在 `maven` 镜像内 `package`**，全部在服务器完成，本地不上传任何包。

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
| npm 安装慢/超时 | 服务器需能访问 npm registry；可在前端 Dockerfile 内加 `npm config set registry` 或更换镜像源 |
| maven 依赖下载慢 | 服务器需能访问 Maven Central；可在后端 Dockerfile 内配置国内镜像源 |
| pgvector 函数缺失 | 确认用的是 `pgvector/pgvector:pg17` 镜像；建表前需 `CREATE EXTENSION IF NOT EXISTS vector;` |

## 六、后续迭代

- 接入 MinIO、agent、training/serving 动态容器时，取消 `docker-compose.yml` 中 `admin` 服务的 `docker.sock` 挂载注释，并补充对应 service 定义。
- 生产建议加 HTTPS（在 `front` 前再挂一层 `nginx-proxy` + `acme-companion` 或自建 cert）。
