# V4 原生 Function Calling 重构设计（2026-08-09）

> 决策人：@xuhonglin · 状态：已确认（suggest_action 退役 / 思考开关放聊天输入框 / 走 Git 远程构建）

## 1. 背景与目标

worker 侧 agent（ops-agent-core）目前用 `deepseek-reasoner` + 「输出契约 JSON」协议：
模型按 SystemMessage 注入的工具清单输出 `{"tool":..,"args":..}` JSON，代码再 `_parse_tool_calls` 硬解析。
该协议是为"不支持 tools 参数的推理模型"设计的，存在以下问题：

- **协议脆弱**：JSON 前缀猜测（`_looks_like_json`/`json_mode`）、裸 JSON 兜底补发、`_is_tool_round` 标记，全是对不原生工具支持的补偿。
- **安全边界是"提示词级"**：写工具注册进 registry 且 `tools_node` 会直接执行 `http.call`，只靠 prompt 约束模型不调——模型违规即绕过审批。
- **模型名已过期**：`deepseek-reasoner` 于 2026-07-24 停用，过渡期指向 V4；V4 原生支持 thinking + function calling。
- **V4 协议差异**：带 tools 的轮次**必须回传 reasoning_content**（否则 400），与旧 reasoner「必须剥离」的约定正好相反。

**目标**：切 `deepseek-v4-flash`，全面迁移到**原生 function calling**（bind_tools + ToolMessage），
写操作收敛为**细粒度 `approve_<写工具>` 审批工具**（模型永远无法直接执行写操作），
思考模式做成**前端聊天输入框上方开关**（随消息实时传，无需重启 worker）。

## 2. 方案总览

```
前端 AgentAssistant.vue           admin                        worker (ops-agent-core)
┌─────────────────────┐   REST   ┌──────────────────┐  gRPC   ┌──────────────────────────┐
│ 输入框上方「深度思考」开关 │ ───────▶│ AgentTaskService │ ───────▶│ TaskDispatch             │
│ dispatch({query,      │  chat   │  setReasoning    │  Task   │  .reasoning_enabled      │
│   reasoningEnabled}) │         │  Enabled(...)     │  Dispatch│  handle_dispatch          │
└─────────────────────┘         └──────────────────┘         │  → LLMRuntime.select()     │
                                                              │  → bind_tools(tools)      │
                                                              │  → astream (thinking/delta)│
                                                              │  → tools_node (ToolMessage)│
                                                              └──────────────────────────┘
```

- **proto**：`TaskDispatch` 新增 `bool reasoning_enabled = 13`（两端同步 agent.proto + 重新生成）。
- **worker LLM**：`LLMRuntime` 持有 thinking/fast 两个 `ChatDeepSeek` 实例，按 `reasoning_enabled` 选择；
  强度 `reasoning_effort` 走 env（`DEEPSEEK_REASONING_EFFORT`，默认 max）。
- **工具层**：`bind_tools` 只注册 = 只读工具（registry）+ 内置工具（plan_create/plan_update）+ **`approve_<写工具>` 审批工具**；
  写工具本体**不进 tools**，模型无调用路径。suggest_action 退役。

## 3. worker 改造（ops-agent-core）

### 3.1 config.py

```python
deepseek_model: str = "deepseek-v4-flash"      # 默认值变更
deepseek_reasoning_effort: str = "max"          # 新增：thinking 强度 high|max（thinking 开时生效）
# thinking 开关不再全局配置 —— 由 TaskDispatch.reasoning_enabled 按任务控制
```

### 3.2 main.py — LLMRuntime

```python
class LLMRuntime:
    """按任务开关选择 thinking/fast LLM 实例；配置热更新时重建并替换内部引用。"""
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._llms: dict[bool, ChatDeepSeek] = {}
    def select(self, reasoning: bool) -> ChatDeepSeek:   # 懒加载两个变体
        ...
    # thinking 版：model_kwargs={"thinking": {"type": "enabled"}, "reasoning_effort": cfg.…}
    # fast 版：    model_kwargs={"thinking": {"type": "disabled"}}
```

`amain()` 以 `llm_runtime` 替换裸 `llm` 传入 `core.handle_dispatch` / `tracker`；
`handle_execute` 的总结调用用 `select(False)`（总结无需思考，省 token）。

### 3.3 graph.py — 核心协议重构

**新增**：

```python
BUILTIN_TOOL_SCHEMAS: dict[str, dict] = {plan_create, plan_update}   # OpenAI function schema（从原 build_tool_prompt 的 JSON Schema 文本转 dict）

def build_openai_tools(registry) -> list[dict]:
    """bind_tools 用的完整工具列表：
       = registry.schemas()（只读工具，is_write 过滤掉）
       + plan_create / plan_update schema
       + approve_<写工具名> schema（parameters 复用写工具本体 schema，
         追加 plan_id/step_no/retry_of 可选参数；description 说明"提出审批建议，批准后由系统执行"）"""

def approve_tool_name(write_tool_name: str) -> str:  # "training_create" -> "approve_training_create"
def action_type_from_approve(name: str) -> str:      # 逆映射
```

**agent_node**：

- 不再注入 `build_tool_prompt`；`llm = llm_runtime.select(ctx.reasoning_enabled).bind_tools(build_openai_tools(registry))`。
- 流式：`reasoning_content` 增量实时发 thinking（保留）；`content` 增量**不再做 JSON 前缀猜测**——
  检测到 chunk 的 `tool_call_chunks` 非空即视为工具轮（content 通常为空/说明文字，不展示）；
  纯正文轮正常发 delta。
- 收尾：`merged.tool_calls` 非空 → `pending_tools = merged.tool_calls`（[{id,name,args}]，langchain 原生结构），
  `additional_kwargs["reasoning_content"]` 保留聚合链（回传必需）；否则最终回答。
- **删除 `_strip_reasoning`**：assistant 消息的 reasoning_content 原样回传（V4 带 tools 轮次硬要求）。
- **删除** `_parse_tool_calls` / `_looks_like_json` / `_JSON_BLOCK_RE` / json_mode / pending 缓存逻辑。

**tools_node**：

```python
for tc in pending_tools:            # tc: {id, name, args}
    if name == "plan_create": ... 本地 handler
    elif name == "plan_update": ... 本地 handler
    elif name.startswith("approve_"): handle_suggest_action(store, ctx, args, action_type=从名推导)  # 落 PENDING 建议
    else: 只读工具 → http.call(tool, args, ctx)
    tool_msgs.append(ToolMessage(content=json.dumps(result), tool_call_id=tc["id"]))   # 原生 tool 角色
```

写工具在 tools_node **不再有任何执行路径**（不进 tools，也不在分发里兜底）。

### 3.4 decision.py — 同协议迁移

- `run_decision_round`：`llm.select(True).bind_tools(tools)` + `resp.tool_calls` 判空 +
  `ToolMessage` 回填循环；`DECISION_SYSTEM` 删「输出 JSON」说明，改「使用工具函数」。
- `_execute_decision_tool`：`approve_*` 前缀走 `handle_suggest_action`；其余只读工具走 HTTP；
  写工具本体仍 403 拦截（防御，正常不会出现）。

### 3.5 core.py — prompt 与结论提取

- `SYSTEM_PROMPT`「工具使用规范」重写：删 JSON 契约段，改为「使用系统提供的工具函数」；
  写操作说明改指向 `approve_*`（例：要发起训练 → 调用 `approve_training_create`）；删「每次只调用一个工具」（V4 支持并行）。
- `_extract_conclusion`：`_is_tool_round` 标记改为判断 `getattr(m, "tool_calls", None)` 是否为空。
- `_extract_reasoning` 不变。

## 4. proto + admin 改造

### 4.1 proto（两端同步）

```proto
message TaskDispatch {
  ...
  bool reasoning_enabled = 13;   // chat：前端思考开关；execute 忽略
}
```

Python 侧本地 grpc_tools 重新生成 `agent_pb2.py` / `agent_pb2_grpc.py`；
Java 侧远端 mvn 构建时自动生成（走 Git 部署）。

### 4.2 admin（ops-agent-admin）

- `AgentTaskService.dispatchChat(...)` 增加 `reasoningEnabled` 参数 → `TaskDispatch.Builder.setReasoningEnabled(reasoningEnabled)`。
- 聊天 REST 接口（AgentConversationController 的 chat/dispatch）接收 `reasoningEnabled`（默认 true）。
- 新增字段默认 true，避免影响既有调用。

## 5. 前端改造（ops-agent-front）

- `AgentAssistant.vue` 输入框上方（L210 `.pa-3` 容器内）加「深度思考」开关：
  `v-switch`（size small / 隐藏细节），状态放 agent store（`reasoningEnabled`，默认 true，持久化 localStorage）。
- `stores/agent.js`：`dispatch({query, reasoningEnabled})` 透传；`send()` 带当前开关值。
- `api/agent.js`：chat 请求体加 `reasoning: boolean`。

## 6. 测试

- `tests/test_graph.py`：mock LLM 从「返回 JSON 文本 AIMessage」改为「返回带 `tool_calls` 的
  AIMessage + ToolMessage 序列」；新增断言：approve_* 工具只落建议不执行写操作；reasoning_content 保留回传。
- `tests/test_agent_core.py`：dispatch 链路断言 reasoning_enabled 透传；_extract_conclusion 新逻辑。
- `tests/test_decision.py`（如有）：decision 轮 bind_tools 循环。
- E2E（scripts/agent_e2e_runner.py）：fake worker 全链路回归（fake LLM 仍走 mock）。

## 7. 风险与兼容

| 风险 | 缓解 |
|---|---|
| `reasoning_content` 回传遗漏 → 400 | graph 消息流全程保留 additional_kwargs；新增单测断言回传存在 |
| 写工具被模型直接调用 | 写工具本体不进 tools，tools_node 无执行路径，双保险 |
| ChatDeepSeek 对 model_kwargs 透传行为差异 | main.py 装配后先打日志校验请求体；E2E fake worker 覆盖 |
| 前端开关影响旧消息流 | 开关仅影响后续发送，历史消息渲染不变 |
| `deepseek-reasoner` 模型名已停用 | 默认值直接改 `deepseek-v4-flash`，env 旧值部署时由 deploy.sh ensure 覆盖 |

## 8. 实施清单

1. proto：两端 agent.proto + Python 重新生成
2. worker：config.py / main.py(LLMRuntime) / graph.py / decision.py / core.py
3. admin：AgentTaskService + 聊天 controller
4. 前端：AgentAssistant.vue + stores/agent.js + api/agent.js
5. 测试更新 + 本地 pytest
6. commit + push → 远端 deploy.sh 构建部署（admin + core + front）
7. 部署后验证：新 API/行为存在、agent 对话全链路
