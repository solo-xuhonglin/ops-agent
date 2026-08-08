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

# ===== 2. 准备 .env（自动补全缺失/占位符密钥，并落盘凭据）=====
if [ ! -f .env ]; then
  echo "==> 未找到 .env，已从 .env.example 复制"
  cp .env.example .env
fi

# 部署凭证落盘路径（含数据库等已有信息），可用 CRED_FILE 环境变量覆盖
CRED_FILE="${CRED_FILE:-/root/ops-agent-credentials.txt}"

# 生成/保留一个密钥：若 .env 缺失该键或其值为占位符，则生成随机串并写回 .env
ensure_secret() {
  local key="$1" current val
  current="$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
  if [ -z "$current" ] || printf '%s' "$current" | grep -qi "CHANGE_ME"; then
    val="$(openssl rand -base64 24 2>/dev/null | tr -dc 'A-Za-z0-9' | head -c 32 || true)"
    if grep -qE "^${key}=" .env; then
      sed -i "s|^${key}=.*|${key}=${val}|" .env
    else
      printf '%s=%s\n' "$key" "$val" >> .env
    fi
    echo "$val"
  else
    echo "$current"
  fi
}

# 确保某键存在（带默认值），不覆盖已有值
ensure_present() {
  local key="$1" val="$2"
  if ! grep -qE "^${key}=" .env; then
    printf '%s=%s\n' "$key" "$val" >> .env
  fi
}

DB_PASSWORD="$(ensure_secret DB_PASSWORD)"
JWT_SECRET="$(ensure_secret JWT_SECRET)"
MINIO_ROOT_PASSWORD="$(ensure_secret MINIO_ROOT_PASSWORD)"
ensure_present MINIO_ROOT_USER minioadmin
ensure_present MINIO_BUCKET datasets
ensure_present MINIO_PORT 9000
ensure_present MINIO_CONSOLE_PORT 9001

# 读取最终值用于写凭据文件
SERV_IP="$(grep -E '^SERVER_IP=' .env | cut -d= -f2- || true)"
[ -z "$SERV_IP" ] && SERV_IP="118.195.145.247"
DB_USER="$(grep -E '^DB_USERNAME=' .env | cut -d= -f2- || true)"
PG_DB="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- || true)"
PG_PORT="$(grep -E '^POSTGRES_PORT=' .env | cut -d= -f2- || true)"
ADM_PORT="$(grep -E '^ADMIN_PORT=' .env | cut -d= -f2- || true)"
HTTP_PORT_VAL="$(grep -E '^HTTP_PORT=' .env | cut -d= -f2- || true)"
MINIO_USER="$(grep -E '^MINIO_ROOT_USER=' .env | cut -d= -f2- || true)"
MINIO_PASS="$(grep -E '^MINIO_ROOT_PASSWORD=' .env | cut -d= -f2- || true)"

cat > "$CRED_FILE" <<EOF
===== ops-agent 部署凭证（请妥善保管）=====
生成时间: $(date '+%Y-%m-%d %H:%M:%S')
服务器IP: $SERV_IP

数据库信息:
  DB_USERNAME: $DB_USER
  DB_PASSWORD: $DB_PASSWORD
  POSTGRES_DB: $PG_DB
  POSTGRES_PORT: $PG_PORT

JWT密钥:
  JWT_SECRET: $JWT_SECRET

MinIO 对象存储:
  MINIO_ROOT_USER: $MINIO_USER
  MINIO_ROOT_PASSWORD: $MINIO_PASS
  MINIO_BUCKET: datasets
  MinIO 控制台: http://$SERV_IP:${MINIO_CONSOLE_PORT:-9001}
  (后端 admin 通过内部服务名 minio:9000 访问，无需对外暴露)

前端访问: http://$SERV_IP:${HTTP_PORT_VAL:-80}
后端API:  http://$SERV_IP:${ADM_PORT:-8080}/api
默认账号: admin / admin123（首次登录后请立即修改）
============================================
EOF
echo "==> 部署凭证已写入 $CRED_FILE"

# ===== 3. 宿主机编译（依赖缓存于本地 node_modules / 项目内 .m2）=====
# Maven 本地仓库放在项目目录下（已被 .gitignore 忽略，可缓存复用）；产物在 target/（标准位置）
M2_REPO="$PROJECT_DIR/.m2"
ADMIN_DIR="$PROJECT_DIR/ops-agent-admin"

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
mvn -B clean package -DskipTests \
  -Dmaven.repo.local="$M2_REPO"
if ! ls "$ADMIN_DIR"/target/*.jar >/dev/null 2>&1; then
  echo "ERROR: 未找到打包产物 $ADMIN_DIR/target/*.jar，后端构建失败" >&2
  exit 1
fi

cd "$PROJECT_DIR"

# ===== 4. 预构建训练镜像（profile tools，不随 up 启动，仅供 admin 动态实例化）=====
echo "==> 预构建训练镜像 ops-agent-train:latest"
docker compose --env-file .env --profile tools build train

# ===== 5. 仅拷贝产物打包镜像并启动（秒级）=====
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
echo "提示: 演示账号 admin/admin123（管理员）、user/user123（普通用户）"
