"""在远程服务器后台执行一键部署脚本 deploy.sh（detached）。

等价于登录服务器执行：
  cd $REMOTE_DIR && ./deploy.sh

用法：
  python scripts/ssh_deploy.py                 # 常规部署（训练镜像无变更时自动跳过构建）
  python scripts/ssh_deploy.py --force-train   # 强制重建训练镜像（FORCE_BUILD_TRAIN=1）

说明：
  - 通过 setsid 脱离 SSH 通道，命令立即返回，不会因部署耗时长而中断。
  - 部署日志写到远程 /tmp/deploy.log，进度用：
        python scripts/ssh_poll.py --log /tmp/deploy.log
  - 部署流程：git pull → 前端 npm build → 后端 mvn package → 训练镜像（按需）→ compose up。

注意：deploy.sh 自身会执行 git pull。若本次提交改动了 deploy.sh，
建议先单独 pull 再运行，避免脚本在执行过程中被覆盖。

连接参数来自环境变量或项目根 deploy-remote.env，详见同目录 _conn.py。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import run_detached

DEPLOY_LOG = "/tmp/deploy.log"


def main():
    parser = argparse.ArgumentParser(description="在远程后台运行 deploy.sh")
    parser.add_argument(
        "--force-train",
        action="store_true",
        help="强制重建训练镜像（默认无变更时跳过）",
    )
    args = parser.parse_args()

    env_prefix = "FORCE_BUILD_TRAIN=1 " if args.force_train else ""
    if args.force_train:
        print(">> FORCE_BUILD_TRAIN=1 (训练镜像将强制重建)")

    run_detached(f"chmod +x deploy.sh && {env_prefix}./deploy.sh", DEPLOY_LOG)
    print(f">> follow: python scripts/ssh_poll.py --log {DEPLOY_LOG}")


if __name__ == "__main__":
    main()
