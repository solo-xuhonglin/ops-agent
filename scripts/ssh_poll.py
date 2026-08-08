"""轮询训练镜像构建进度与结果。

输出：远程构建日志末尾 + 是否已生成 ops-agent-train 镜像。

用法：
  python scripts/ssh_poll.py

典型流程：
  python scripts/ssh_train_build.py   # 启动后台构建
  python scripts/ssh_poll.py          # 反复执行直到出现镜像（NOT BUILT YET -> 镜像名）

连接参数来自环境变量或项目根 deploy-remote.env，详见同目录 _conn.py。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import get_client, target

BUILD_LOG = "/tmp/train_build.log"

CMD = (
    "echo '===== BUILD LOG (tail) ====='; "
    f"tail -n 25 {BUILD_LOG} 2>/dev/null; "
    "echo '===== TRAIN IMAGE ====='; "
    "docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -i train || echo 'NOT BUILT YET'; "
    "echo '===== DONE ====='"
)


def main():
    client = get_client()
    print(f">> target: {target()}")
    try:
        stdin, stdout, stderr = client.exec_command(CMD, timeout=30)
        print(stdout.read().decode("utf-8", "ignore"))
        err = stderr.read().decode("utf-8", "ignore").strip()
        if err:
            print("ERR:", err)
    finally:
        client.close()


if __name__ == "__main__":
    main()
