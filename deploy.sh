#!/usr/bin/env bash
# ops-agent 一键部署脚本（全程在服务器上：拉取代码 → 宿主机编译 → 拷贝产物打包 → 启动）
# 策略：依赖安装/编译在宿主机完成（node_modules、项目内 .m2 可缓存），Docker 仅拷贝 build 产物，镜像构建秒级
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

# ===== 3. 宿主机编译（依赖缓存于本地 node_modules / 项目内 .m2）=====
# Maven 本地仓库与产物均放在项目目录下（已被 .gitignore 忽略，可缓存复用）
M2_REPO="$PROJECT_DIR/.m2"
ADMIN_DIR="$PROJECT_DIR/ops-agent-admin"
ADMIN_BUILD="$ADMIN_DIR/build"

echo "==> 前端编译（npm install && build，依赖缓存于 node_modules）"
cd "$PROJECT_DIR/ops-agent-front"
npm install
npm run build
if [ ! -d "$PROJECT_DIR/ops-agent-front/dist" ] || [ -z "$(ls -A "$PROJECT_DIR/ops-agent-front/dist" 2>/dev/null)" ]; then
  echo "ERROR: 前端构建产物 dist/ 缺失，前端构建失败" >&2
  exit 1
fi

echo "==> 后端打包（mvn package，依赖缓存于 $M2_REPO，产物在 target/）"
cd "$ADMIN_DIR"
mvn -q -B clean package -DskipTests \
  -Dmaven.repo.local="$M2_REPO"

# spring-boot repackage 会把 jar 写回 target/，需复制到 build/ 供 Dockerfile COPY
mkdir -p "$ADMIN_BUILD"
cp -v "$ADMIN_DIR"/target/*.jar "$ADMIN_BUILD"/
if ! ls "$ADMIN_BUILD"/*.jar >/dev/null 2>&1; then
  echo "ERROR: 未找到打包产物 $ADMIN_DIR/target/*.jar，后端构建失败" >&2
  exit 1
fi

cd "$PROJECT_DIR"

# ===== 4. 仅拷贝产物打包镜像并启动（秒级）=====
echo "==> 加载 .env，构建轻量镜像并启动"
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
