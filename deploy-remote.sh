#!/usr/bin/env bash
# deploy-remote.sh — Local build, remote docker build & deploy (NO GitHub needed).
#
# Strategy: build artifacts on the local machine (maven jar + vite dist), rsync
# the project (minus caches and the secret .env) to the remote host, then let
# the remote docker compose build images and recreate the admin/front containers.
# This avoids pulling from GitHub, so it works even when GitHub egress is blocked.
#
# Usage:
#   ./deploy-remote.sh                 # uses defaults / env overrides below
#   SSHPASS=xxx ./deploy-remote.sh     # password auth via sshpass
#   REMOTE_HOST=1.2.3.4 ./deploy-remote.sh
#
# Env overrides:
#   LOCAL_DIR      project root (default: this script's dir)
#   REMOTE_HOST    remote IP/host   (default 118.195.145.247)
#   REMOTE_USER    remote ssh user  (default root)
#   REMOTE_PORT    ssh port         (default 22)
#   REMOTE_DIR     remote path      (default /opt/ops-agent)
#   MAVEN_REPO     local maven repo (default D:/XuDevOps/maven-repository)
#   SSHPASS        ssh password; if set, sshpass is used (key auth otherwise)
set -euo pipefail

LOCAL_DIR="${LOCAL_DIR:-$(cd "$(dirname "$0")" && pwd)}"

# Source deploy-remote.env if present (records SSH host/user/pass for the test server)
if [ -f "$LOCAL_DIR/deploy-remote.env" ]; then
  # shellcheck disable=SC1090
  . "$LOCAL_DIR/deploy-remote.env"
fi
REMOTE_HOST="${REMOTE_HOST:-118.195.145.247}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_PORT="${REMOTE_PORT:-22}"
REMOTE_DIR="${REMOTE_DIR:-/opt/ops-agent}"
ADMIN_DIR="$LOCAL_DIR/ops-agent-admin"
FRONT_DIR="$LOCAL_DIR/ops-agent-front"
MAVEN_REPO="${MAVEN_REPO:-D:/XuDevOps/maven-repository}"

# ---- ssh / rsync command selection ----
SSH_BASE="ssh -p $REMOTE_PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
RSYNC_SSH="ssh -p $REMOTE_PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
if [ -n "${SSHPASS:-}" ]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "ERROR: SSHPASS is set but sshpass is not installed." >&2
    exit 1
  fi
  SSH="sshpass -e $SSH_BASE"
  RSYNC_BIN="sshpass -e rsync"
else
  SSH="$SSH_BASE"
  RSYNC_BIN="rsync"
fi

echo "==> target: $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"

# ===== [1/4] backend package (local) =====
echo "==> [1/4] building backend (mvn clean package -DskipTests)"
if [ ! -d "$ADMIN_DIR" ]; then echo "ERROR: $ADMIN_DIR not found"; exit 1; fi
cd "$ADMIN_DIR"
MVN_REPO_OPT="${MAVEN_REPO:+-Dmaven.repo.local=$MAVEN_REPO}"
# build against the D: drive local repo; NOT offline so missing plugins can be fetched
mvn -B clean package -DskipTests $MVN_REPO_OPT
JAR=$(ls "$ADMIN_DIR"/target/*.jar 2>/dev/null | head -1)
if [ -z "$JAR" ]; then echo "ERROR: backend jar not produced"; exit 1; fi
echo "    built: $JAR"

# ===== [2/4] frontend build (local) =====
echo "==> [2/4] building frontend (npm install && npm run build)"
if [ ! -d "$FRONT_DIR" ]; then echo "ERROR: $FRONT_DIR not found"; exit 1; fi
cd "$FRONT_DIR"
npm install
npm run build
if [ ! -d "$FRONT_DIR/dist" ] || [ -z "$(ls -A "$FRONT_DIR/dist" 2>/dev/null)" ]; then
  echo "ERROR: frontend dist/ missing or empty"; exit 1
fi
echo "    built: $FRONT_DIR/dist"

# ===== [3/4] rsync project to remote (exclude caches + secret .env) =====
echo "==> [3/4] syncing project to remote (excluding .git/node_modules/.m2/.env)"
$RSYNC_BIN -az --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='.m2' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.venv' \
  --exclude='.env' \
  -e "$RSYNC_SSH" \
  "$LOCAL_DIR/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"

# ===== [4/4] remote docker build & deploy =====
echo "==> [4/4] remote docker build & deploy (admin + front)"
$SSH "$REMOTE_USER@$REMOTE_HOST" \
  "cd '$REMOTE_DIR' && docker compose --env-file .env up -d --build admin front && sleep 6 && docker compose ps"

echo "==> deploy complete"
echo "    backend API: http://$REMOTE_HOST:8080/api"
echo "    frontend:    http://$REMOTE_HOST/"
