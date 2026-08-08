# 05 · 远程运维脚本（scripts/）

> 本目录存放连接部署服务器的 Python 运维脚本，基于 `paramiko` SSH 实现。
> 适用场景：无需手动敲 SSH 命令即可查看部署状态、后台构建训练镜像、轮询构建进度。
>
> **脚本中不包含任何服务器地址与口令**，全部由外部配置注入（见「三、连接配置」）。

## 一、前置条件

1. Python 环境已安装 `paramiko`。
   - 本机使用受管运行时（推荐）：
     `C:/Users/wangc/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
   - 该 venv 已含 `paramiko`；若缺失可执行：
     `C:/Users/wangc/.workbuddy/binaries/python/envs/default/Scripts/pip.exe install paramiko`
2. 已在项目根创建 `deploy-remote.env`（见下一节），或已导出对应环境变量。

## 二、脚本清单

| 脚本 | 作用 | 关键输出 |
|------|------|----------|
| `_conn.py` | **共用辅助模块**，不是入口。解析连接配置、封装 SSH 连接与后台执行；导出 `get_client()` / `target()` / `run_detached()` / `REMOTE_DIR`。其它脚本均 `import` 它。 | — |
| `ssh_status.py` | 查看远程部署状态 | git 当前版本、运行中容器（`docker compose ps`）、已构建镜像列表、磁盘占用 |
| `ssh_deploy.py` | 在远程**后台**执行一键部署 `deploy.sh` | 立即返回 `LAUNCHED`；日志在远程 `/tmp/deploy.log` |
| `ssh_train_build.py` | 在远程**后台**单独重建训练镜像 `ops-agent-train:latest` | 立即返回 `LAUNCHED`；日志在远程 `/tmp/train_build.log` |
| `ssh_poll.py` | 轮询任一后台任务的日志与结果（部署 / 构建通用） | 日志末尾 + 当前 ops-agent 镜像与容器状态 |

`ssh_deploy.py` 支持 `--force-train` 强制重建训练镜像；
`ssh_poll.py` 支持 `--log <远程日志路径>`（默认 `/tmp/train_build.log`）与 `-n <行数>`（默认 25）。

> 后台任务通过 `setsid` 脱离 SSH 通道。偶尔通道会延迟关闭导致读取超时，
> `run_detached()` 已对此容错并提示 —— 此时进程仍在运行，用 `ssh_poll.py` 确认即可。

## 三、连接配置

所有脚本通过 `_conn.py` 建立连接。配置解析优先级：

**环境变量 > 项目根 `deploy-remote.env` > 内置默认值（仅限非敏感项）**

| 配置项 | 含义 | 是否必填 | 默认值 |
|--------|------|----------|--------|
| `REMOTE_HOST` | 服务器 IP / 域名 | **必填** | 无（缺失即报错退出） |
| `SSHPASS` | SSH 登录密码 | **必填** | 无（缺失即报错退出） |
| `REMOTE_USER` | 登录用户 | 可选 | `root` |
| `REMOTE_PORT` | SSH 端口 | 可选 | `22` |
| `REMOTE_DIR` | 服务器上的项目目录 | 可选 | `/opt/ops-agent` |

### 初始化

```bash
cp deploy-remote.env.example deploy-remote.env   # 然后填入真实值
```

`deploy-remote.env` 已被 `.gitignore` 忽略，**不会**进入版本库。
这份配置由 `deploy-remote.sh`、`deploy-remote.py`、`scripts/*.py` 三者共用，只需维护一处。

### 临时覆盖（切换目标服务器）

```powershell
# PowerShell
$env:REMOTE_HOST = "192.168.1.10"
$env:SSHPASS = "******"
python scripts/ssh_status.py
```

```bash
# bash
REMOTE_HOST=192.168.1.10 SSHPASS='******' python scripts/ssh_status.py
```

## 四、用法示例

下文用 `PY` 代指受管 Python：
`C:/Users/wangc/.workbuddy/binaries/python/envs/default/Scripts/python.exe`

```bash
# 1) 查看当前部署状态
$PY scripts/ssh_status.py

# 2) 一键部署（最常用）
$PY scripts/ssh_deploy.py
$PY scripts/ssh_poll.py --log /tmp/deploy.log -n 40    # 反复执行直到出现「部署完成」

# 3) 只重建训练镜像，不跑完整部署
$PY scripts/ssh_train_build.py
$PY scripts/ssh_poll.py                                 # 默认看 /tmp/train_build.log

# 4) 强制重建训练镜像后完整部署（如需刷新基础镜像）
$PY scripts/ssh_deploy.py --force-train
```

每个脚本启动时会打印 `>> target: user@host:port`（不含口令），便于确认连的是哪台机器。

## 五、典型运维流程

1. **改了后端/前端/训练代码** → push 到 GitHub 后跑 `ssh_deploy.py`，
   再用 `ssh_poll.py --log /tmp/deploy.log` 跟踪到「部署完成」。
   训练镜像是否重建由 `deploy.sh` 按内容哈希自动决定（见 `04-deploy.md`）。
2. **只想重建训练镜像**（不动 admin/front） → `ssh_train_build.py` + `ssh_poll.py`。
3. **日常巡检** → `ssh_status.py` 一眼看版本、容器、镜像、磁盘。

> ⚠️ 若本次提交改动了 `deploy.sh` 自身，建议先在服务器单独 `git pull` 再运行部署，
> 避免 bash 边读边执行时脚本文件被 pull 覆盖导致行为异常。

## 六、安全约定

- **禁止**在任何脚本源码中写入服务器地址、账号或口令；一律走 `deploy-remote.env` 或环境变量。
- `deploy-remote.env` 与 `.env` 均已 gitignore；提交前可用
  `git status --short` 与 `git diff --cached` 复核，确认没有凭据被带入。
- 更适合团队/生产的做法：改用 SSH 密钥登录，或从密钥管理服务读取口令，
  避免密码以明文形式落盘。
- 这些脚本仅用于本地或内网运维，不具备任何鉴权与审计能力。
