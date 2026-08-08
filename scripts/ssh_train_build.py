"""在远程后台预构建训练镜像 ops-agent-train:latest（detached）。

构建使用 compose 的 tools profile：
  docker compose --env-file .env --profile tools build train

用法：
  python scripts/ssh_train_build.py

说明：
  - 使用 setsid 完全脱离 SSH 通道，命令立即返回（LAUNCHED）。
  - 构建日志写到远程 /tmp/train_build.log，进度请用 scripts/ssh_poll.py 查看。
  - 仅训练相关代码（ops-agent-data-train/）变更时需要重跑；admin/front 由 deploy.sh 负责。

连接参数来自环境变量或项目根 deploy-remote.env，详见同目录 _conn.py。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import REMOTE_DIR, get_client, target

BUILD_LOG = "/tmp/train_build.log"

CMD = (
    f"cd {REMOTE_DIR} && "
    f"setsid bash -c 'cd {REMOTE_DIR} && "
    f"docker compose --env-file .env --profile tools build train "
    f"> {BUILD_LOG} 2>&1' </dev/null >/dev/null 2>&1 & "
    f"echo LAUNCHED"
)


def main():
    client = get_client()
    print(f">> target: {target()} {REMOTE_DIR}")
    try:
        stdin, stdout, stderr = client.exec_command(CMD, timeout=20)
        print(stdout.read().decode("utf-8", "ignore").strip())
        err = stderr.read().decode("utf-8", "ignore").strip()
        if err:
            print("ERR:", err)
    finally:
        client.close()
    print(f">> train build detached on remote; log at {BUILD_LOG}")


if __name__ == "__main__":
    main()
