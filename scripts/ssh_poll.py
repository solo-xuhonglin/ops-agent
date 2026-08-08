"""轮询远程后台任务的日志与结果（训练镜像构建 / 一键部署通用）。

输出：指定日志文件末尾 + 当前镜像与容器状态。

用法：
  python scripts/ssh_poll.py                              # 默认看训练构建日志
  python scripts/ssh_poll.py --log /tmp/deploy.log        # 看部署日志
  python scripts/ssh_poll.py --log /tmp/deploy.log -n 60  # 多看几行

典型流程：
  python scripts/ssh_train_build.py   # 启动后台构建
  python scripts/ssh_poll.py          # 反复执行直到出现镜像（NOT BUILT YET -> 镜像名）

  python scripts/ssh_deploy.py                       # 启动后台部署
  python scripts/ssh_poll.py --log /tmp/deploy.log   # 反复执行直到出现「部署完成」

连接参数来自环境变量或 scripts/ssh.env，详见同目录 _conn.py。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import get_client, target

DEFAULT_LOG = "/tmp/train_build.log"


def build_cmd(log_path: str, lines: int) -> str:
    return (
        f"echo '===== LOG tail({lines}): {log_path} ====='; "
        f"tail -n {lines} {log_path} 2>/dev/null || echo '(log not found yet)'; "
        "echo '===== IMAGES ====='; "
        "docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' "
        "| grep -Ei 'ops-agent' || echo 'NO ops-agent IMAGE YET'; "
        "echo '===== CONTAINERS ====='; "
        "docker ps --format '{{.Names}}\t{{.Status}}' | grep -i ops-agent || echo 'NONE'; "
        "echo '===== DONE ====='"
    )


def main():
    parser = argparse.ArgumentParser(description="轮询远程后台任务日志")
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"远程日志路径（默认 {DEFAULT_LOG}）")
    parser.add_argument("-n", "--lines", type=int, default=25, help="显示日志末尾行数（默认 25）")
    args = parser.parse_args()

    client = get_client()
    print(f">> target: {target()}")
    try:
        stdin, stdout, stderr = client.exec_command(build_cmd(args.log, args.lines), timeout=30)
        print(stdout.read().decode("utf-8", "ignore"))
        err = stderr.read().decode("utf-8", "ignore").strip()
        if err:
            print("ERR:", err)
    finally:
        client.close()


if __name__ == "__main__":
    main()
