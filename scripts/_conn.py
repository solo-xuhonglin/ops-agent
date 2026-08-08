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
# 凭据优先放用户主目录 ~/.ops-agent/deploy-remote.env（项目目录内容易被清理/同步覆盖），项目根为兼容兜底
HOME_CFG = os.path.join(os.path.expanduser("~"), ".ops-agent", "deploy-remote.env")
ENV_FILE = HOME_CFG if os.path.exists(HOME_CFG) else os.path.join(PROJECT_ROOT, "deploy-remote.env")


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


def run_detached(inner_cmd, log_path):
    """在远程以 setsid 完全脱离 SSH 通道的方式后台执行命令。

    参数：
      inner_cmd  在 REMOTE_DIR 下执行的命令字符串
      log_path   远程日志文件路径，命令的 stdout/stderr 都写入这里

    返回：
      True 表示确认收到 LAUNCHED；False 表示通道未及时回读
      （后台进程已通过 setsid 脱离，通常仍在正常运行，用轮询脚本确认即可）。

    说明：后台进程偶尔会让 SSH 通道延迟关闭，导致 stdout.read() 超时。
    这里对超时做容错，不视为启动失败。
    """
    import socket

    cmd = (
        f"cd {REMOTE_DIR} && "
        f"setsid bash -c 'cd {REMOTE_DIR} && {inner_cmd} > {log_path} 2>&1' "
        f"</dev/null >/dev/null 2>&1 & "
        f"echo LAUNCHED"
    )
    client = get_client()
    print(f">> target: {target()} {REMOTE_DIR}")
    launched = False
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
        try:
            out = stdout.read().decode("utf-8", "ignore").strip()
            if out:
                print(out)
            launched = "LAUNCHED" in out
            err = stderr.read().decode("utf-8", "ignore").strip()
            if err:
                print("ERR:", err)
        except (socket.timeout, Exception) as exc:  # noqa: BLE001
            print(f">> channel read timed out ({type(exc).__name__}); "
                  f"process was detached and is most likely running")
    finally:
        client.close()
    print(f">> detached on remote; log at {log_path}")
    return launched
