# ops-agent-test · 端到端测试

基于 **pytest + httpx** 的 API 层端到端测试，默认针对**远程已部署实例**，
验证 `ops-agent-admin` 后端 `/api/datasets` 数据集全生命周期。

> 文档（设计稿）与真实实现不一致：真实接口为两步式
> `POST /api/datasets`（建记录，自动触发天气采集）→ `POST /api/datasets/{id}/collect`
> （显式重新采集到 MinIO `weather.csv`），文件上传接口（`/file`）已移除。
> 本套件已按**真实实现**编写。

## 1. 目录结构

```
ops-agent-test/
  requirements.txt
  pytest.ini
  .env.example
  conftest.py                 # 登录 fixture / make_dataset(自带清理) / ready_model / reader_client
  src/opsagent_client.py      # 后端 HTTP 客户端封装
  tests/
    test_dataset_lifecycle.py # Tier1 元数据 CRUD
    test_dataset_file.py      # Tier2 天气采集 + MinIO 落盘真实验证
    test_dataset_negative.py  # 鉴权/异常用例
    test_model_training.py    # 模型版本 + 训练任务（含真实跑一轮）
    test_model_training_negative.py
    test_serving.py           # 模型服务（部署/推理/下线全链路）
    test_serving_negative.py
    test_agent.py             # Agent 模块契约（tools/tasks/suggestions）
    test_agent_negative.py    # 401 / READONLY 角色 403
    test_agent_worker.py      # Agent 全链路（需 fake worker，AGENT_E2E=1）
    support/fake_worker.py    # 受控 gRPC fake worker（在服务器容器内运行）
```

## 2. 配置

```bash
cd ops-agent-test
cp .env.example .env          # 填入 BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD
```

- `BASE_URL`：后端 API 基址，默认 `http://localhost:8080/api`。
  连测试服务器时在 `.env` 里改为 `http://<服务器IP>:8080/api`。
- 账号需具备 `dataset:read` 与 `dataset:write`（admin 默认具备）。

## 3. 安装与运行

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

仅跑某层：

```bash
pytest -m tier1        # 元数据 CRUD
pytest -m tier2        # 天气采集/落盘链路
pytest -m negative     # 鉴权/异常
```

## 4. "真采集 / 真删除" 验证说明

- **采集真成功**：`POST /api/datasets/{id}/collect` 返回 200 + 数据集状态 `READY`、
  `rowCount > 0`、`objectKey == {id}/weather.csv`；预签名 URL 成功签发（证明后端↔MinIO
  通路正常，天气数据确实落到 MinIO 路径）。
  - 不下载/字节比对：后端用 `MINIO_ENDPOINT=http://minio:9000`（docker 内部地址）签发
    URL，测试机直接下载常因 DNS 不通而失败；`objectKey` 正确 + 签名成功已足够证明
    采集链路端到端跑通。
- **删除真成功**：`DatasetService.delete` **已修复**——删除数据库记录的同时会清理
  关联的 MinIO 对象（best-effort，异常仅告警不阻断）。测试验证：DELETE 后
  `GET /api/datasets/{id}` 返回 404、列表不再包含该 id、文件关联 `GET .../file/url` 也 404。

## 5. 数据隔离

`make_dataset` fixture 用 `uuid` 生成唯一名称，并在 `try/finally` 中无论如何 `DELETE`
清理，绝不污染远程数据；用例间无顺序依赖。

## 6. Agent 模块测试

- **契约/负向**（`test_agent.py` / `test_agent_negative.py`）：tools 启停与校验、tasks/
  suggestions 列表与 404、401、403 —— 无 worker 依赖，`pytest -v` 直接跑。
- **全链路**（`test_agent_worker.py`）：真实 gRPC 闭环——dispatch → TaskDispatch →
  worker 回事件/结果 → SUCCEEDED → suggestion 落库 → approve → grantKey 推送 →
  execute_suggestion → EXECUTED / REJECTED。需要一个**受控 fake worker**（真实
  ops-agent-core 会调 LLM，行为不确定），由编排脚本隔离运行：

  ```bash
  # 项目根目录（复用 scripts/_conn.py 的 SSH 凭据）
  python scripts/agent_e2e_runner.py
  ```

  脚本会：短暂 `docker stop ops-agent-agent`（注册表清空）→ 起 fake worker 容器
  （复用 `ops-agent-core:latest` 镜像与 gRPC stub，连 docker 网络内 `admin:9090`）
  → 等注册完成 → 本地跑 `AGENT_E2E=1` 的 agent 测试 → **自动恢复真实 agent**并清理
  fake worker，最后打印 worker 收到的 grant/任务日志作为证据。
- **权限说明**：种子用户 `user/user123` 是 OPERATOR（业务读写，代码设计如此），
  不能用于 403 测试；`conftest.py` 的 `reader_client` 会用 admin 动态创建一个
  READONLY 角色用户（只有 `*:read`），测完即删。
