"""查看远程部署状态：当前 git 版本、运行中的容器、已构建镜像、磁盘占用。

用法：
  python scripts/ssh_status.py

连接参数来自环境变量或 scripts/ssh.env，详见同目录 _conn.py。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import REMOTE_DIR, get_client, target

CMD = f"""
echo "===== GIT ====="
cd {REMOTE_DIR} && git log -1 --oneline 2>/dev/null; git status --short 2>/dev/null | head
echo "===== DOCKER COMPOSE PS ====="
cd {REMOTE_DIR} && docker compose --env-file .env ps 2>/dev/null || docker compose ps 2>/dev/null
echo "===== ALL IMAGES ====="
docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}  {{{{.Size}}}}'
echo "===== DISK ====="
df -h {REMOTE_DIR} 2>/dev/null | tail -1
echo "===== DONE ====="
"""


def main():
    client = get_client()
    print(f">> target: {target()} {REMOTE_DIR}")
    try:
        stdin, stdout, stderr = client.exec_command(CMD, timeout=120)
        print(stdout.read().decode("utf-8", "ignore"))
        err = stderr.read().decode("utf-8", "ignore").strip()
        if err:
            print("=== STDERR ===\n" + err)
    finally:
        client.close()


if __name__ == "__main__":
    main()
