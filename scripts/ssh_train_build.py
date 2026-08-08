"""在远程后台单独构建训练镜像 ops-agent-train:latest（detached）。

构建使用 compose 的 tools profile：
  docker compose --env-file .env --profile tools build train

用法：
  python scripts/ssh_train_build.py

说明：
  - 通过 setsid 脱离 SSH 通道，命令立即返回。
  - 构建日志写到远程 /tmp/train_build.log，进度用 scripts/ssh_poll.py 查看。
  - 常规部署走 deploy.sh 即可（它会按内容哈希决定是否重建训练镜像）；
    本脚本用于只想单独重建训练镜像、不想跑完整部署的场景。
  - 本脚本总是执行真实构建，但 Docker 层缓存仍然生效：
    仅 train.py 变更时只重跑末尾 COPY 层，秒级完成。

连接参数来自环境变量或 scripts/ssh.env，详见同目录 _conn.py。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import run_detached

BUILD_LOG = "/tmp/train_build.log"


def main():
    run_detached(
        "docker compose --env-file .env --profile tools build train",
        BUILD_LOG,
    )
    print(f">> follow: python scripts/ssh_poll.py --log {BUILD_LOG}")


if __name__ == "__main__":
    main()
