#!/usr/bin/env bash
# ops-agent 一键部署脚本（在服务器上执行）
set -euo pipefail

echo "==> 检查 Docker / Compose"
docker --version
docker compose version || docker-compose --version

if [ ! -f .env ]; then
  echo "==> 未找到 .env，已从 .env.example 复制，请按需修改密钥后再重新运行"
  cp .env.example .env
  echo "    编辑 .env 修改 DB_PASSWORD / JWT_SECRET / SERVER_IP 后执行 ./deploy.sh"
  exit 1
fi

echo "==> 加载 .env 并构建启动"
docker compose --env-file .env up -d --build

echo "==> 等待服务就绪"
sleep 5
docker compose ps

echo "==> 部署完成"
echo "前端:    http://<服务器IP>:${HTTP_PORT:-80}"
echo "后端API: http://<服务器IP>:${ADMIN_PORT:-8080}/api"
echo "数据库:  ${DB_USERNAME:-opsagent} @ ${POSTGRES_PORT:-5432}"
echo ""
echo "提示: 首次启动后请修改默认账号 admin/admin123（见 docs/01-architecture.md）"
