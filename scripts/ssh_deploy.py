"""在远程服务器后台执行一键部署脚本 deploy.sh（detached）。

等价于登录服务器执行：
  cd $REMOTE_DIR && ./deploy.sh [选项] [服务...]

用法：
  python scripts/ssh_deploy.py                       # 全量部署
  python scripts/ssh_deploy.py admin                 # 只更新后端
  python scripts/ssh_deploy.py front admin           # 只更新前后端
  python scripts/ssh_deploy.py train --force-train   # 强制重建训练镜像
  python scripts/ssh_deploy.py --no-pull admin       # 用远程当前工作树重建后端
  python scripts/ssh_deploy.py --no-build --no-deps admin   # 仅重启 admin 容器

可选服务：all / admin / front / train / infra / postgres / minio
（infra 展开为 postgres + minio + minio-init；train 只构建镜像，不启动容器）

说明：
  - 通过 setsid 脱离 SSH 通道，命令立即返回，不会因部署耗时长而中断。
  - 部署日志写到远程 /tmp/deploy.log，跟进度用：
        python scripts/ssh_poll.py --log /tmp/deploy.log
  - 全量流程：git pull → 前端 npm build → 后端 mvn package → 训练镜像（按需）→ compose up。
    指定服务时只执行该服务对应的环节。

注意：deploy.sh 自身会执行 git pull。若本次提交改动了 deploy.sh，
建议先单独 pull 再用 --no-pull 运行，避免脚本在执行过程中被覆盖。

连接参数来自环境变量或项目根 deploy-remote.env，详见同目录 _conn.py。
"""
import argparse
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import run_detached

DEPLOY_LOG = "/tmp/deploy.log"
SERVICES = ("all", "admin", "front", "train", "infra", "postgres", "minio")


def main():
    parser = argparse.ArgumentParser(
        description="在远程后台运行 deploy.sh，支持按服务粒度部署",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "服务: " + " / ".join(SERVICES) + "\n"
            "示例:\n"
            "  python scripts/ssh_deploy.py admin\n"
            "  python scripts/ssh_deploy.py --no-pull front admin\n"
            "  python scripts/ssh_deploy.py --build-only train\n"
        ),
    )
    # 不用 argparse 的 choices：nargs="*" 时它会把默认值 [] 也当作取值去校验而报错，
    # 这里手动校验，报错信息也更清楚。
    parser.add_argument(
        "targets", nargs="*", default=[],
        metavar="SERVICE", help="要部署的服务，可多选；省略即全量",
    )
    parser.add_argument("--no-pull", action="store_true",
                        help="远程跳过 git pull，使用当前工作树代码")
    parser.add_argument("--build-only", action="store_true",
                        help="只编译/构建镜像，不执行 compose up")
    parser.add_argument("--no-build", action="store_true",
                        help="跳过编译与镜像构建，仅重启容器")
    parser.add_argument("--no-deps", action="store_true",
                        help="compose up 时不连带启动依赖服务")
    parser.add_argument("--force-train", action="store_true",
                        help="强制重建训练镜像（默认无变更时跳过）")
    args = parser.parse_args()

    unknown = [t for t in args.targets if t not in SERVICES]
    if unknown:
        parser.error(
            f"unknown service: {', '.join(unknown)} "
            f"(choose from {', '.join(SERVICES)})"
        )

    flags = []
    if args.no_pull:
        flags.append("--no-pull")
    if args.build_only:
        flags.append("--build-only")
    if args.no_build:
        flags.append("--no-build")
    if args.no_deps:
        flags.append("--no-deps")
    if args.force_train:
        flags.append("--force-train")

    argv = flags + list(args.targets)
    cmd = "./deploy.sh" + ("" if not argv else " " + " ".join(shlex.quote(a) for a in argv))

    print(f">> remote: {cmd}")
    run_detached(f"chmod +x deploy.sh && {cmd}", DEPLOY_LOG)
    print(f">> follow: python scripts/ssh_poll.py --log {DEPLOY_LOG}")


if __name__ == "__main__":
    main()
