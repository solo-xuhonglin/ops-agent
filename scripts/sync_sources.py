#!/usr/bin/env python3
"""将本地源码同步到远程 /opt/ops-agent 工作树（供 ./deploy.sh --no-pull 使用）。

适用场景：本机无法 push GitHub（沙箱网络受限）时，直接把手头代码推到服务器，
再用 --no-pull 让 deploy.sh 基于当前工作树构建部署。

用法：
  python scripts/sync_sources.py

覆盖范围（本地为准）：
  ops-agent-admin/{pom.xml,Dockerfile,src/**}
  ops-agent-data-train/{train.py,Dockerfile,requirements.txt}
  docker-compose.yml  deploy.sh  .env.example

连接参数来自环境变量或 scripts/ssh.env，详见同目录 _conn.py。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import get_client, REMOTE_DIR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (本地相对路径, 远程相对路径)；目录将递归上传
ITEMS = [
    ("ops-agent-admin/pom.xml", "ops-agent-admin/pom.xml"),
    ("ops-agent-admin/Dockerfile", "ops-agent-admin/Dockerfile"),
    ("ops-agent-admin/src", "ops-agent-admin/src"),
    ("ops-agent-core", "ops-agent-core"),
    ("ops-agent-data-train/train.py", "ops-agent-data-train/train.py"),
    ("ops-agent-data-train/Dockerfile", "ops-agent-data-train/Dockerfile"),
    ("ops-agent-data-train/requirements.txt", "ops-agent-data-train/requirements.txt"),
    ("docker-compose.yml", "docker-compose.yml"),
    ("deploy.sh", "deploy.sh"),
    (".env.example", ".env.example"),
]


def sftp_put_dir(sftp, local_dir, remote_dir):
    for root, _dirs, files in os.walk(local_dir):
        rel = os.path.relpath(root, local_dir)
        target = remote_dir if rel == "." else f"{remote_dir}/{rel.replace(os.sep, '/')}"
        try:
            sftp.stat(target)
        except FileNotFoundError:
            sftp.mkdir(target)
        for f in files:
            lp = os.path.join(root, f)
            rp = f"{target}/{f}"
            sftp.put(lp, rp)
            print("  put", rp)


def main():
    client = get_client()
    sftp = client.open_sftp()
    try:
        for local_rel, remote_rel in ITEMS:
            lp = os.path.join(ROOT, local_rel)
            rp = f"{REMOTE_DIR}/{remote_rel}"
            if not os.path.exists(lp):
                print("SKIP (missing locally):", local_rel)
                continue
            if os.path.isdir(lp):
                print("== sync dir:", local_rel)
                sftp_put_dir(sftp, lp, rp)
            else:
                sftp.put(lp, rp)
                print("== put:", remote_rel)
        print("SYNC_OK")
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
