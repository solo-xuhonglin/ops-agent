#!/usr/bin/env bash
# ops-agent 一键部署脚本（全程在服务器上：拉取代码 → 编译 → 构建镜像 → 启动）
# 不依赖本地上传任何构建产物（dist/target 均在服务器内生成）
set -euo pipefail

# ===== 可配置项 =====
REPO_URL="${REPO_URL:-https://github.com/solo-xuhonglin/ops-agent.git}"
PROJECT_DIR="${PROJECT_DIR:-/opt/ops-agent}"
BRANCH="${BRANCH:-main}"

echo "==> 检查 Docker / Compose"
docker --version
docker compose version || docker-compose --version

# ===== 1. 获取代码（首次 clone，后续 pull）=====
if [ ! -d "$PROJECT_DIR/.git" ]; then
  echo "==> 首次部署：clone 仓库到 $PROJECT_DIR"
  git clone -b "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
else
  echo "==> 更新代码：git pull ($BRANCH)"
  git -C "$PROJECT_DIR" pull --ff-only origin "$BRANCH"
fi
cd "$PROJECT_DIR"

# ===== 2. 准备 .env =====
if [ ! -f .env ]; then
  echo "==> 未找到 .env，已从 .env.example 复制，请按需修改密钥后再重新运行"
  cp .env.example .env
  echo "    编辑 .env 修改 DB_PASSWORD / JWT_SECRET / SERVER_IP 后执行 ./deploy.sh"
  exit 1
fi

# ===== 3. 服务器内编译 + 构建镜像 + 启动 =====
# 前端 Dockerfile 内含 npm install && build；后端 Dockerfile 内含 maven package
echo "==> 加载 .env，服务器内编译并构建镜像"
docker compose --env-file .env up -d --build

echo "==> 等待服务就绪"
sleep 8
docker compose ps

echo "==> 部署完成"
echo "前端:    http://<服务器IP>:${HTTP_PORT:-80}"
echo "后端API: http://<服务器IP>:${ADMIN_PORT:-8080}/api"
echo "数据库:  ${DB_USERNAME:-opsagent} @ ${POSTGRES_PORT:-5432}"
echo ""
echo "提示: 首次启动后请修改默认账号 admin/admin123（见 docs/01-architecture.md）"
