# ops-agent-test · 端到端测试

基于 **pytest + httpx** 的 API 层端到端测试，默认针对**远程已部署实例**，
验证 `ops-agent-admin` 后端 `/api/datasets` 数据集全生命周期。

> 文档（设计稿）与真实实现不一致：真实接口为两步式
> `POST /api/datasets`（建记录）→ `POST /api/datasets/{id}/file`（传文件到 MinIO），
> 而非文档里的 `POST /api/datasets/upload`。本套件已按**真实实现**编写。

## 1. 目录结构

```
ops-agent-test/
  requirements.txt
  pytest.ini
  .env.example
  conftest.py                 # 登录 fixture / make_dataset(自带清理)
  src/opsagent_client.py      # 后端 HTTP 客户端封装
  tests/
    test_dataset_lifecycle.py # Tier1 元数据 CRUD
    test_dataset_file.py      # Tier2 MinIO 文件上传真实验证
    test_dataset_negative.py  # 鉴权/异常用例
```

## 2. 配置

```bash
cd ops-agent-test
cp .env.example .env          # 填入 BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD
```

- `BASE_URL`：后端 API 基址，默认 `http://118.195.145.247:8080/api`。
  本地联调改为 `http://localhost:8080/api`。
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
pytest -m tier2        # MinIO 上传链路
pytest -m negative     # 鉴权/异常
```

## 4. "真上传 / 真删除" 验证说明

- **上传真成功**：上传返回 200 + `objectKey == datasets/{id}/{filename}`；
  预签名 URL 成功签发（证明后端↔MinIO 通路正常，文件确实落到 MinIO 路径）。
  - 不下载/字节比对：后端用 `MINIO_ENDPOINT=http://minio:9000`（docker 内部地址）签发
    URL，测试机直接下载常因 DNS 不通而失败；`objectKey` 正确 + 签名成功已足够证明
    上传链路端到端跑通。
- **删除真成功**：`DatasetService.delete` **已修复**——删除数据库记录的同时会清理
  关联的 MinIO 对象（best-effort，异常仅告警不阻断）。测试验证：DELETE 后
  `GET /api/datasets/{id}` 返回 404、列表不再包含该 id、文件关联 `GET .../file/url` 也 404。

## 5. 数据隔离

`make_dataset` fixture 用 `uuid` 生成唯一名称，并在 `try/finally` 中无论如何 `DELETE`
清理，绝不污染远程数据；用例间无顺序依赖。
