#!/usr/bin/env bash
# ops-agent 一键部署脚本（全程在服务器上：拉取代码 → 宿主机编译 → 拷贝产物打包 → 启动）
# 策略：依赖安装/编译在宿主机完成（node_modules、项目内 .m2 可缓存），Docker 仅拷贝 build 产物，镜像构建秒级
#
# 支持按服务粒度部署，避免每次都全量重编。用法见 usage() 或 ./deploy.sh --help
set -euo pipefail

# ===== 可配置项 =====
REPO_URL="${REPO_URL:-https://github.com/solo-xuhonglin/ops-agent.git}"
PROJECT_DIR="${PROJECT_DIR:-/opt/ops-agent}"
BRANCH="${BRANCH:-main}"

usage() {
  cat <<'USAGE'
用法: ./deploy.sh [选项] [服务...]

服务（可多选，省略等同 all）:
  all         全部：前端 + 后端 + 训练镜像 + 基础设施
  admin       后端 Spring Boot（mvn package → 重建 admin 镜像 → 重启）
  front       前端 Vue（npm build → 重建 front 镜像 → 重启）
  train       训练镜像 ops-agent-train:latest（仅构建，不启动容器）
  infra       基础设施：postgres + minio + minio-init（仅启动，无需编译）
  postgres    仅数据库
  minio       仅对象存储

选项:
  --no-pull        跳过 git pull（改动了 deploy.sh 自身时建议手动 pull 后用此项）
  --build-only     只编译/构建镜像，不执行 compose up
  --no-build       跳过编译与镜像构建，仅重启容器（等价于 restart）
  --no-deps        compose up 时不连带启动依赖服务（如只重启 admin 不碰 postgres）
  --force-train    强制重建训练镜像（等价 FORCE_BUILD_TRAIN=1）
  -h, --help       显示本帮助

示例:
  ./deploy.sh                      # 全量部署（默认）
  ./deploy.sh admin                # 只更新后端
  ./deploy.sh front admin          # 只更新前后端，不动训练镜像
  ./deploy.sh --no-pull admin      # 用当前工作树代码重建后端
  ./deploy.sh --build-only train   # 只构建训练镜像
  ./deploy.sh --no-build --no-deps admin   # 仅重启 admin 容器
USAGE
}

# ===== 参数解析 =====
TARGETS=()
DO_PULL=1
DO_BUILD=1
DO_UP=1
NO_DEPS=0

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)    usage; exit 0 ;;
    --no-pull)    DO_PULL=0 ;;
    --build-only) DO_UP=0 ;;
    --no-build)   DO_BUILD=0 ;;
    --no-deps)    NO_DEPS=1 ;;
    --force-train) FORCE_BUILD_TRAIN=1 ;;
    all|admin|front|train|infra|postgres|minio) TARGETS+=("$1") ;;
    backend)      TARGETS+=("admin") ;;
    frontend)     TARGETS+=("front") ;;
    -*)           echo "ERROR: 未知选项 $1（用 --help 查看用法）" >&2; exit 1 ;;
    *)            echo "ERROR: 未知服务 $1（用 --help 查看可选服务）" >&2; exit 1 ;;
  esac
  shift
done

[ ${#TARGETS[@]} -eq 0 ] && TARGETS=("all")

# infra 展开为具体服务
_expanded=()
for t in "${TARGETS[@]}"; do
  if [ "$t" = "infra" ]; then
    _expanded+=("postgres" "minio" "minio-init")
  else
    _expanded+=("$t")
  fi
done
TARGETS=("${_expanded[@]}")

has_target() {
  for t in "${TARGETS[@]}"; do
    [ "$t" = "all" ] && return 0
    [ "$t" = "$1" ] && return 0
  done
  return 1
}

echo "==> 目标服务: ${TARGETS[*]}"

echo "==> 检查 Docker / Compose"
docker --version
docker compose version || docker-compose --version

# ===== 1. 获取代码（首次 clone，后续 pull）=====
if [ ! -d "$PROJECT_DIR/.git" ]; then
  echo "==> 首次部署：clone 仓库到 $PROJECT_DIR"
  git clone -b "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
elif [ "$DO_PULL" = "1" ]; then
  echo "==> 更新代码：git pull ($BRANCH)"
  git -C "$PROJECT_DIR" pull --ff-only origin "$BRANCH"
else
  echo "==> 跳过 git pull（--no-pull），使用当前工作树代码"
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
ensure_present MINIO_MODEL_BUCKET models
ensure_present MINIO_LOG_BUCKET logs
ensure_present MINIO_PORT 9000
ensure_present MINIO_CONSOLE_PORT 9001

# 读取最终值用于写凭据文件
SERV_IP="$(grep -E '^SERVER_IP=' .env | cut -d= -f2- || true)"
# 未在 .env 指定时自动探测本机地址，不硬编码任何 IP
[ -z "$SERV_IP" ] && SERV_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "$SERV_IP" ] && SERV_IP="unknown"
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

build_front() {
  echo "==> 前端编译（npm install && build，依赖缓存于 node_modules）"
  cd "$PROJECT_DIR/ops-agent-front"
  npm install
  npm run build
  if [ ! -d "$PROJECT_DIR/ops-agent-front/dist" ] || [ -z "$(ls -A "$PROJECT_DIR/ops-agent-front/dist" 2>/dev/null)" ]; then
    echo "ERROR: 前端构建产物 dist/ 缺失，前端构建失败" >&2
    exit 1
  fi
  cd "$PROJECT_DIR"
}

build_admin() {
  echo "==> 后端打包（mvn package，依赖缓存于 $M2_REPO，产物在 target/）"
  cd "$ADMIN_DIR"
  mvn -B clean package -DskipTests \
    -Dmaven.repo.local="$M2_REPO"
  if ! ls "$ADMIN_DIR"/target/*.jar >/dev/null 2>&1; then
    echo "ERROR: 未找到打包产物 $ADMIN_DIR/target/*.jar，后端构建失败" >&2
    exit 1
  fi
  cd "$PROJECT_DIR"
}

# ===== 4. 训练镜像（profile tools，不随 up 启动，仅供 admin 动态实例化）=====
# 训练镜像体积大（约 1.9GB，含 CPU 版 torch），无变更时跳过构建以缩短部署时间。
# 判定依据：镜像已存在，且训练构建上下文（Dockerfile/requirements.txt/train.py）内容哈希未变。
# 强制重建：FORCE_BUILD_TRAIN=1 ./deploy.sh 或 ./deploy.sh --force-train
build_train() {
  local TRAIN_IMAGE="ops-agent-train:latest"
  local TRAIN_CTX="$PROJECT_DIR/ops-agent-data-train"
  local DEPLOY_CACHE="$PROJECT_DIR/.deploy-cache"
  local TRAIN_STAMP="$DEPLOY_CACHE/train-image.sha256"
  mkdir -p "$DEPLOY_CACHE"

  local TRAIN_HASH
  TRAIN_HASH="$(cat "$TRAIN_CTX/Dockerfile" "$TRAIN_CTX/requirements.txt" "$TRAIN_CTX/train.py" 2>/dev/null \
    | sha256sum | awk '{print $1}')"

  if [ -z "${FORCE_BUILD_TRAIN:-}" ] \
     && docker image inspect "$TRAIN_IMAGE" >/dev/null 2>&1 \
     && [ -f "$TRAIN_STAMP" ] \
     && [ "$(cat "$TRAIN_STAMP")" = "$TRAIN_HASH" ]; then
    echo "==> 训练镜像 $TRAIN_IMAGE 已是最新，跳过构建（强制重建：./deploy.sh --force-train）"
  else
    echo "==> 构建训练镜像 $TRAIN_IMAGE（首次部署或训练代码/依赖有变更）"
    docker compose --env-file .env --profile tools build train
    printf '%s' "$TRAIN_HASH" > "$TRAIN_STAMP"
  fi
}

if [ "$DO_BUILD" = "1" ]; then
  if has_target front; then build_front; fi
  if has_target admin; then build_admin; fi
  if has_target train; then build_train; fi
else
  echo "==> 跳过编译与镜像构建（--no-build）"
fi

# ===== 5. 仅拷贝产物打包镜像并启动（秒级）=====
# train 属 profiles:[tools]，只构建不启动，因此不进入 up 列表
UP_SERVICES=()
if has_target all; then
  UP_ALL=1
else
  UP_ALL=0
  for t in "${TARGETS[@]}"; do
    [ "$t" = "train" ] && continue
    UP_SERVICES+=("$t")
  done
fi

if [ "$DO_UP" != "1" ]; then
  echo "==> 跳过 compose up（--build-only）"
elif [ "$UP_ALL" = "1" ]; then
  echo "==> 加载 .env，构建轻量镜像并启动（全部服务）"
  docker compose --env-file .env up -d --build
elif [ ${#UP_SERVICES[@]} -eq 0 ]; then
  echo "==> 无需启动的服务（目标仅含 train，训练镜像只构建不常驻）"
else
  UP_FLAGS=(-d)
  [ "$DO_BUILD" = "1" ] && UP_FLAGS+=(--build)
  [ "$NO_DEPS" = "1" ] && UP_FLAGS+=(--no-deps)
  echo "==> 启动指定服务: ${UP_SERVICES[*]}（flags: ${UP_FLAGS[*]}）"
  docker compose --env-file .env up "${UP_FLAGS[@]}" "${UP_SERVICES[@]}"
fi

if [ "$DO_UP" = "1" ]; then
  echo "==> 等待服务就绪"
  sleep 8
  docker compose ps
fi

echo "==> 部署完成（目标: ${TARGETS[*]}）"
echo "前端:    http://<服务器IP>:${HTTP_PORT:-80}"
echo "后端API: http://<服务器IP>:${ADMIN_PORT:-8080}/api"
echo "数据库:  ${DB_USERNAME:-opsagent} @ ${POSTGRES_PORT:-5432}"
echo ""
echo "提示: 演示账号 admin/admin123（管理员）、user/user123（普通用户）"
