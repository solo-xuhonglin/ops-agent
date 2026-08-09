# Agent 审批建议去重设计（2026-08-09）

## 背景

LLM 在同一轮回复里、或跨轮反复调用同一个 `approve_<写工具>`，导致 `agent_suggestions`
落多行 PENDING，用户界面上刷出多张内容完全一样的审批卡。根因是 `insert_suggestion`
是裸 INSERT、无任何幂等约束，`approve_*` 分支也没有重复检测。

目标：**同一条业务申请，库里只有一行，界面上只有一张卡**，同时让模型尽快收敛、别再刷。

## 1. 去重键与自然语义

判定"两条申请是同一条"的自然键：

```
(conversation_id, action_type, target_type, target_id, params, retry_of)
```

**视为重复 → 复用已有行**，需同时满足：

- 自然键全等（`params` 按 jsonb 语义等值，key 顺序/空白无关）
- 已有行状态 ∈ `{PENDING, APPROVED, EXECUTING}`（"还活着"的申请）

**不视为重复 → 允许新建行**：

- 已关闭状态（`EXECUTED/REJECTED/FAILED/EXPIRED/CANCELLED`）：上一次已结束，
  LLM 重新提出同款 = 全新请求（典型的"上次失败，换参重试"）
- 自然键任一维度不同（不同 target / 不同 params / 不同 retry_of）

### 为什么键里没有 plan_id / step_no

把它们纳入自然键反而**削弱**去重：模型重复提交时最容易漂移的恰恰是它自己填的
`step_no`（第一次填 2、第二次填 3），同 target 同 params 也会逃出去重。
而业务语义上"同一会话内、对同一目标、同一动作、同一参数的**活跃**申请只该有一条"
与它挂在哪个 step 无关。

不会误伤"plan 里 step2 和 step5 都要启动同一个训练"：step2 那条走完即 `EXECUTED`
（关闭态不参与去重），step5 能正常新建。

### 为什么 retry_of 入键，而不是"带 retry_of 就跳过去重"

跳过意味着显式重试完全绕过去重，模型连发两次同一个 `retry_of=X` 又是两张卡。
纳入键（NULL 参与比较）后：

| 场景 | 结果 |
| --- | --- |
| 两条 `retry_of` 均为空的同款 | 合并 |
| 两条 `retry_of=X` 的同款 | 合并（新增保护） |
| `retry_of=X` vs `retry_of` 为空 | 不合并（显式重试保留独立行） |

## 2. params 比对：jsonb 语义等值，不加 hash 列

`agent_suggestions.params` 是 **TEXT**，由 `json.dumps(..., ensure_ascii=False)` 写入，
字符串直接比不可靠（key 顺序会变）。两种方案：

| 方案 | 代价 |
| --- | --- |
| 新增 `params_hash varchar(64)` + 应用层 sha256(canonical_json) | 需要 DDL 迁移 + JPA 实体同步（`kind` 列那次 NOT NULL 翻车的同类风险） |
| **`NULLIF(params,'')::jsonb` 语义比较**（采用） | 零 DDL、零实体改动 |

选后者：PG 的 jsonb 是解析后的二进制格式，key 已排序、空白已归一化，
`jsonb = jsonb` 天然等价于 canonical json 比较。

**脏数据防护**：用 `WITH open_rows AS MATERIALIZED (...)` 先物化候选行，
保证 `::jsonb` cast 只作用于"同会话 + 同动作 + 同目标 + 开放态"的少量行——
即便历史行里存在非法 JSON，也不会炸掉整条查询（PG 不保证 WHERE 子句求值顺序，
CTE MATERIALIZED 是 PG12+ 的显式优化屏障，线上是 PG17）。

**索引**：

```sql
CREATE INDEX idx_agent_sug_dedup
  ON agent_suggestions(conversation_id, action_type, target_id, status);
```

把候选行收敛到个位数量级，jsonb 比对开销可忽略。

## 3. 落地位置：一处查询，同时做硬兜底与教模型

原计划是 A（store 幂等插入）+ B（`tools_node` 里 approve_* 分支前置查重）两处。
实现时合并成一处，因为 **`handle_suggest_action` 是 `approve_*` 唯一的落库入口**，
在 `tools_node` 再写一遍 pre-check 只是重复代码和重复查询。

```
TaskStore.find_open_duplicate(s) -> Optional[sid]     # 唯一的查重 SQL
TaskStore.insert_suggestion(s)   -> (sid, created)    # A：命中即复用，不写库
handle_suggest_action(...)                            # B：created=False 时
  ├─ created=True  → 推 suggestion_created SSE，body={"suggestion_id": sid}
  └─ created=False → 不推事件（卡已在界面上），body 带自然语言提示：
       "该 {action} 申请已存在（suggestion_id=...），正在等待审批或执行中，
        请勿重复提交。请等待审批结果，或继续推进后续步骤。"
```

- **A（硬保证）**：查重发生在 INSERT 之前，同轮并发与跨轮重复都拦得下，
  是"用户可见多张卡"的兜底。即便模型无视提示，库里也只有一行。
- **B（教模型收敛）**：返回体里的 `duplicate: true` + 中文 note 让模型立刻知道
  该转去等待或推进下一步，而不是继续刷同一条申请。

`tools_node` 是 `for tc in pending_tools` 串行处理，同一条 assistant 消息里的
两个相同 `approve_*` 会先后执行——第一个插入后，第二个的查重能查到它。

## 4. 数据流与影响面

```
LLM approve_xxx
  → tools_node（send_event tool_call）
  → handle_suggest_action
  → insert_suggestion → find_open_duplicate
        命中 → 复用 sid，不写库，不推事件，返回 duplicate note
        未命中 → INSERT 1 行 → 推 suggestion_created
  → send_event tool_result
  → 前端 upsertApprovalRow 按 sid 渲染（幂等 upsert）
```

**前端零改动**：`upsertApprovalRow` 已按 `suggestionId` 去重，去重命中时不推
`suggestion_created`，界面自然只有一张卡。

**改动文件**：

| 文件 | 改动 |
| --- | --- |
| `ops-agent-core/app/agent/task_store.py` | 新增 `find_open_duplicate`；`insert_suggestion` 改幂等、返回 `(sid, created)` |
| `ops-agent-core/app/agent/graph.py` | `handle_suggest_action` 按 `created` 分支：推事件 or 返回 duplicate note |
| `ops-agent-core/tests/test_task_store.py` | 3 个新用例（去重复用 / 自然键 SQL 形状 / 缺键短路）+ 2 个签名适配 |
| `scripts/agent_tables.sql` | 加 `idx_agent_sug_dedup` |

**迁移**：无 DDL 变更，只需在现网补建索引（幂等）：

```sql
CREATE INDEX IF NOT EXISTS idx_agent_sug_dedup
  ON agent_suggestions(conversation_id, action_type, target_id, status);
```

**部署**：只动 `ops-agent-core` → `./deploy.sh agent`。

## 5. 测试

`ops-agent-core/tests/test_task_store.py`（mock DB，验 SQL 形状与返回契约）：

- 去重命中 → 复用已有 sid、`created=False`、`db.executed == []`
- 自然键 SQL：只看开放态、含 `::jsonb`、含 `retry_of IS NOT DISTINCT FROM`、
  不含 `plan_id`/`step_no`
- 缺 `conversation_id` 或 `action_type` → 短路返回 None，不发查询

后续可在 `ops-agent-test` 补真实库端到端用例：① 同消息 2 个相同 approve → 仅 1 行 PENDING；
② 跨轮相同 approve → 1 行；③ 不同 params / 带 retry_of / EXECUTED 后重提 → 各自独立行。
