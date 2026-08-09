# Audit Log 设计（2026-08-09）

> 状态：实施中。需求来源：为系统增加审计日志界面，记录「写操作 / 执行人 / 是否 Agent 执行 / 参数」。

## 1. 字段（精简版）

`audit_logs` 表（仅系统写入，无写接口）：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGSERIAL PK | |
| action | VARCHAR(64) | 写操作码：`dataset:create` / `serving:deploy` … |
| actor_type | VARCHAR(16) | `USER` / `AGENT`（即"是否 Agent 执行"） |
| actor_name | VARCHAR(128) | 执行人：人类用户名 或 `Agent` |
| approver_name | VARCHAR(128) NULL | agent 写操作的审批人（人类） |
| target_type | VARCHAR(64) NULL | 操作对象类型 |
| target_id | BIGINT NULL | 操作对象 id |
| params | JSONB NULL | 参数（人类=请求体脱敏；agent=建议 params） |
| ip | VARCHAR(64) NULL | 来源 IP |
| created_at | TIMESTAMPTZ | 默认 now() |

索引：`audit_logs(created_at)`、`(actor_type)`、`(action)`。

> 已刻意精简：去掉 `actor_user_id` / `approver_user_id` / `task_id` / `worker_id` / `success`，避免字段膨胀；溯源靠 `params` + `target` 即可满足审计诉求。

## 2. 权限

新增 `audit:read`（查看审计日志），**仅 ADMIN**（加入 `PERMISSION_DESCRIPTIONS`，不进 OPERATOR/READONLY 列表）。

## 3. 采集（两个精准钩子）

- **人类写操作**：`AuditInterceptor`（`HandlerInterceptor`）+ `CachingRequestFilter`（`ContentCachingRequestWrapper`）自动捕获 `/api/**` 下成功的 POST/PUT/DELETE/PATCH，排除 `/api/auth/**` 与带 `X-Agent-Worker` 的请求；`action` 由 path→code 映射推导；参数对 `password/token/grantKey/secret` 等脱敏。
- **Agent 写操作**：在 `GrantCheckAspect` 消费 grantKey 成功后写审计——`actor_type=AGENT`、`actor_name=Agent`、`approver=审批人`（反查 `agent_suggestions.confirmed_by`）、`params`=建议 params、`target` 取执行后返回 id；拦截器对 agent 请求跳过，避免重复。

统一经 `AuditLogService`（异步 fire-and-forget）落库。

## 4. API

`GET /api/audit/logs`（`audit:read`）：分页 + 过滤 `action / actorType / actorName / approverName / targetType / from / to`，返回 `AuditLogDto`。

## 5. 前端

`views/audit/AuditLogList.vue` + 路由 `/audit/logs`（perm `audit:read`）+ 导航「审计日志」。表格列：时间 · 写操作 · 执行人(用户/🤖) · 是否Agent(人工/Agent) · 审批人 · 目标 · 参数(弹窗 JSON) · 结果。工具栏：写操作下拉、执行人搜索、是否Agent、时间范围、刷新。

## 6. 实施清单

- [x] 设计文档
- [ ] entity / repository / service / controller
- [ ] AuditInterceptor + CachingRequestFilter + WebConfig
- [ ] GrantCheckAspect 接入 agent 审计
- [ ] DataInitializer 加 audit:read
- [ ] 更新 docs/02-data-model.md、docs/03-api.md
- [ ] 前端列表页 / 路由 / 导航
- [ ] 提交并推送 GitHub
