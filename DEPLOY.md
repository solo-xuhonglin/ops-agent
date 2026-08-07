# ops-agent 部署指南（腾讯云 118.195.145.247）

> 配套部署文件已生成在仓库根目录：`docker-compose.yml`、`deploy.sh`、`.env`、`.gitignore` 补充；前端 `ops-agent-front/Dockerfile` + `nginx.conf`；后端复用原有 `Dockerfile`。

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

1. 已安装 Docker Engine 与 Docker Compose v2（`docker compose version` 可查）。
   - 腾讯云 Ubuntu 可一键装：
     ```bash
     curl -fsSL https://get.docker.com | sh
     sudo systemctl enable --now docker
     ```
2. 安全组放行端口：`80`（前端）、`8080`（后端，按需）、`5432`（数据库，建议仅内网/VPC 放行，公网勿开）。
3. 本机把仓库整体上传到服务器（含 `dist/` 目录与所有部署文件）：
   ```bash
   # 本地执行（示例，需你填写实际登录方式）
   scp -r ./ops-agent root@118.195.145.247:/opt/ops-agent
   ```

## 二、修改密钥（上线前必做）

编辑 `.env`，至少替换：

- `DB_PASSWORD` —— 强密码
- `JWT_SECRET` —— 至少 32 位随机串，可用 `openssl rand -base64 48` 生成
- `SERVER_IP` / `CORS_ALLOWED_ORIGINS` —— 改成你的服务器公网 IP

> 默认账号 `admin / admin123` 由后端 `DataInitializer` 写入，上线后务必修改（见 `docs/01-architecture.md` 第 4 节）。

## 三、部署

```bash
cd /opt/ops-agent
chmod +x deploy.sh
./deploy.sh
```

脚本会加载 `.env` 并 `docker compose up -d --build` 构建启动全部服务。

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
| pgvector 函数缺失 | 确认用的是 `pgvector/pgvector:pg17` 镜像；建表前需 `CREATE EXTENSION IF NOT EXISTS vector;` |

## 六、后续迭代

- 接入 MinIO、agent、training/serving 动态容器时，取消 `docker-compose.yml` 中 `admin` 服务的 `docker.sock` 挂载注释，并补充对应 service 定义。
- 生产建议加 HTTPS（在 `front` 前再挂一层 `nginx-proxy` + `acme-companion` 或自建 cert）。
