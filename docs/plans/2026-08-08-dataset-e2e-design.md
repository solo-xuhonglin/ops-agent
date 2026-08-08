# 数据集端到端测试设计（2026-08-08）

> 配套： brainstorming 流程产出；实现位于 `ops-agent-test/`。
> 范围：数据集（Dataset）全生命周期 E2E，Python / pytest + httpx，API 层。

## 1. 背景与关键发现

- `docs/03-api.md` 与真实实现**不一致**：文档写 `POST /api/datasets/upload`
  （一次传文件+字段），真实 `DatasetController` 是两步式：
  - `POST /api/datasets`（JSON `CreateRequest`）建记录，`status` 落库为 `READY`，
    未传 `objectKey` 时默认 `weather://<name>`。
  - `POST /api/datasets/{id}/file`（multipart `file`）上传到 MinIO，写 `objectKey`。
  - `GET /api/datasets/{id}/file/url` 取预签名 URL。
  - 另有 `GET/PUT/DELETE /api/datasets[/{id}]`。
- 字段用复数 `regions: List<String>`（非文档里的单数 `region`）。
- 后端信封：`{ code, message, data, timestamp }`，null 字段省略（`@JsonInclude(NON_NULL)`）。
- **发现（已修复）**：`DatasetService.delete` 原仅删 DB 行、**不删 MinIO 对象** → 孤儿文件；
  已改为删除时 best-effort 清理关联 MinIO 对象（跳过 `weather://` 占位键，异常仅告警）。

## 2. 目标

针对**远程已部署实例**（默认 `BASE_URL` 指向服务器，可改 env 联调本地），
用 pytest + httpx 直连 `/api`，覆盖数据集全生命周期；并做到"真上传/真删除"验证。

分层（互不阻塞）：
- **Tier1** 元数据 CRUD：建→列→详→改→删（仅依赖 PG）。
- **Tier2** MinIO 上传链路：上传 + 预签名 URL + 尽力下载比对。
- **negative** 鉴权/异常：无 token→401、删不存在→404、传文件到不存在 id→404。

## 3. 结构与运行

```
ops-agent-test/
  requirements.txt  pytest.ini  .env.example  README.md
  conftest.py      # 登录 fixture（session 级复用 token）、make_dataset（自带清理）
  src/opsagent_client.py  # 后端 HTTP 封装 + dataset 辅助
  tests/test_dataset_lifecycle.py | test_dataset_file.py | test_dataset_negative.py
```
运行：`pip install -r requirements.txt && pytest -v`（或 `-m tier1|tier2|negative`）。

## 4. "真成功"验证策略（用户明确要求）

- **上传真成功**：① 200 + `objectKey == datasets/{id}/{filename}`；② 预签名 URL 成功签发
  （后端↔MinIO 通路 OK）。**不下载/字节比对**：预签名 URL 用 `MINIO_ENDPOINT=http://minio:9000`
  （docker 内部），测试机直连常 DNS 不通；`objectKey` 正确 + 签名成功已足够证明上传端到端跑通。
- **删除真成功（已修复）**：`DatasetService.delete` 现会在删 DB 记录的同时清理关联 MinIO 对象
  （best-effort，异常仅告警）。测试验证 DELETE 后 `GET /{id}` → 404、`list` 不再含该 id、
  `file/url` → 404。
- **数据隔离**：`make_dataset` 用 `uuid` 唯一名，`try/finally` 必清理，不污染远程。

## 5. 后续可扩展

数据集稳定后，可同结构扩展：用户/角色/权限（RBAC）、模型版本、训练任务、部署 endpoint，
以及 UI 层 Playwright 端到端（本次未含）。
