# 提交审批建议后中断模型循环（Human-in-the-Loop 收口）

- 日期：2026-08-09
- 模块：`ops-agent-core/app/agent/graph.py`、`core.py`
- 问题：模型调用 `approve_<写操作名>` 提交审批建议后，决策图循环没有中断，模型继续推理/调用工具，直到 `max_rounds` 才停。

## 根因

`graph.py` 的 LangGraph 循环 `agent → tools → agent …` 仅在模型「不再产出任何 tool_calls」时走 `END`。
`approve_*(...)` 经 `handle_suggest_action` 落库 PENDING 建议后，没有任何「已提交审批」的中断信号，
循环回到 `agent_node`，模型（或人类提示不足时）继续产生工具调用/文本，白白消耗轮次。

## 方案：硬中断 + 一句摘要

在 State 增加 `pending_approval` 标志，落库成功后置位，`agent_node` 据此进入「收口轮」。

### 改动点（graph.py）

1. `AgentState` 增加 `pending_approval: bool`（默认 False）。
2. `tools_node`：本轮若某个 `approve_*` 调用返回 `status==200`（新建或命中去重均算，均表示界面已有开放建议），
   置 `pending_approval=True`；返回 dict 携带该标志。返回 400（缺必填参数）/500（库不可用）时不置位，让模型修复或自然收敛。
3. `agent_node`：进入时若 `state["pending_approval"]` 为 True，则用 `llm.bind_tools([])`（不挂任何工具）再调一次 LLM——
   模型只能输出一段中文摘要，无法再发工具调用，杜绝提交后空转。
4. `run_graph` 初始 state 注入 `pending_approval: False`（chat 与 execute 任务共用入口，均生效）。
5. 路由不变：`tools → agent` 仍无条件；收口轮 `agent` 因无工具可调，产出摘要后 `should_continue` 见 `pending_tools` 为空即 `END`。
   `plan_create`/`plan_update`/`wait_until`/只读查询均不触发该标志，循环行为完全不变。

### 提示词补强（core.py，防御纵深）

- `SYSTEM_PROMPT` 工具规范新增「提交审批后停止」：提交 `approve_*` 后不要再调工具，系统暂停等待人工审批，
  审批通过由独立执行任务续跑计划；提交后只需简短汇报。
- `EXECUTE_LOOP_SYSTEM` 步骤 3 后追加 3b：提出下一步 `approve_*` 后本任务即结束，等待人工审批，无需继续轮询。

## 效果

chat 任务与 execute 任务提交审批建议后立即收口：最后一条 assistant 消息即「已提交审批建议（suggestion_id=xxx），等待人工确认」，
不再空转烧轮次；人工审批通过后由独立 execute 任务续跑计划/步骤。

## 验证

- 新增 `ops-agent-core/app/tests/test_graph_approve_interrupt.py`：用 FakeLLM/FakeRegistry/FakeStore/FakeClient 驱动 `run_graph`，
  断言「模型首轮调 `approve_*` 后，仅再产生一轮无工具的摘要即结束（LLM 总调用次数 == 2）」，证明提交后循环被硬中断。
- 该测试依赖 langgraph/langchain，`pip install -r requirements.txt` 后在容器内运行（本地无依赖，仅做 CI/容器校验）。
