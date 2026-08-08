"""ops-agent 远程运维脚本共用的 SSH 连接辅助。

设计原则：**代码中不出现任何主机地址、账号或口令**。
连接参数按以下优先级解析：

  1) 进程环境变量
  2) 项目根目录的 deploy-remote.env（已被 .gitignore 忽略，
     与 deploy-remote.py / deploy-remote.sh 共用同一份凭据）

配置项：
  REMOTE_HOST   必填  服务器 IP 或域名
  SSHPASS       必填  SSH 登录密码
  REMOTE_USER   可选  登录用户，默认 root
  REMOTE_PORT   可选  SSH 端口，默认 22
  REMOTE_DIR    可选  服务器上的项目目录，默认 /opt/ops-agent

缺少必填项时脚本会直接退出并给出提示，而不是回退到某个内置默认值。
初始化：复制 deploy-remote.env.example 为 deploy-remote.env 并填入真实值。
"""
import os

import paramiko

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(PROJECT_ROOT, "deploy-remote.env")


def _load_env_file(path):
    """解析 KEY=VALUE 形式的 env 文件，忽略注释、空行与首尾引号。"""
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


_FILE_CFG = _load_env_file(ENV_FILE)


def cfg(key, default=None):
    """取配置：环境变量 > deploy-remote.env > default。"""
    return os.environ.get(key) or _FILE_CFG.get(key) or default


HOST = cfg("REMOTE_HOST")
PORT = int(cfg("REMOTE_PORT", "22"))
USER = cfg("REMOTE_USER", "root")
PASS = cfg("SSHPASS")
REMOTE_DIR = cfg("REMOTE_DIR", "/opt/ops-agent")


def _require(name, value):
    """必填项校验，缺失时给出可操作的英文提示并退出。"""
    if not value:
        raise SystemExit(
            f"ERROR: {name} is not configured.\n"
            f"  Set it as an environment variable, or put it in: {ENV_FILE}\n"
            f"  Template: deploy-remote.env.example\n"
            f"  Credentials must never be hardcoded in scripts."
        )


def get_client():
    """建立并返回一个已连接的 paramiko SSHClient。"""
    _require("REMOTE_HOST", HOST)
    _require("SSHPASS", PASS)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        port=PORT,
        username=USER,
        password=PASS,
        timeout=30,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def target():
    """返回 user@host:port 形式的目标描述，供脚本打印（不含口令）。"""
    return f"{USER}@{HOST}:{PORT}"
