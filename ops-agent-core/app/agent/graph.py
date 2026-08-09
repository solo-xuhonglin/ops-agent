"""LangGraph 决策图（M3.5+，标准 LangChain 生态）。

StateGraph: START -> agent(LLM 决策) -> tools(执行工具) -> 有工具调用回 agent / 否则 END
消息体系为 langchain BaseMessage + add_messages reducer（checkpoint 持久化原生兼容）；
LLM 用 langchain_deepseek.ChatDeepSeek（deepseek-v4-flash），**原生 function calling**：
工具列表经 bind_tools 注入（registry 只读工具 + plan_create/plan_update + approve_<写工具>
审批工具；写工具本体不进 tools，模型无直接执行路径）；agent 按需流式调用，
工具调用放 state.pending_tools 传递，工具结果以原生 ToolMessage 回填，
assistant 消息保留 reasoning_content 原样回传（V4 带 tools 轮次硬要求，缺失 400）。

任务上下文（TaskContext）放在 state：同一图实例可被多任务（不同 thread_id）并发 ainvoke。
对外契约：调用方（core.handle_dispatch）仍收 TaskEvent / TaskResult；
agent 节点流式产出（astream）：推理链增量发 thinking、正文增量发 delta（工具轮 content 不展示），
聚合推理链挂最终 AIMessage.additional_kwargs["reasoning_content"]（落库/回传复用）。
"""
import json
import logging
import uuid
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.agent.context import TaskContext
from app.tools.http_client import AdminHttpClient
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.graph")

# 写工具 → (查询工具, 结果对象类型, 结果对象 ID 参数名)：训练/部署是异步接口，agent 据此轮询状态
WRITE_TRACK_MAP: dict[str, tuple[str, str, str]] = {
    "training_create": ("training_get", "training_job", "jobId"),
    "serving_deploy": ("serving_get", "serving_endpoint", "endpointId"),
}


def _extract_object_id(body: Any) -> Optional[int]:
    """从写接口响应 body 提取创建对象的 id（ApiResponse.data.id）。"""
    if not isinstance(body, str):
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    node = data.get("data") if isinstance(data, dict) else None
    if isinstance(node, dict):
        vid = node.get("id")
        return int(vid) if isinstance(vid, (int, float)) or str(vid).isdigit() else None
    return None


async def _maybe_register_tracker(tracker: Any, store: Any, ctx: TaskContext,
                                  tool: Optional[agent_pb2.ToolSchema],
                                  result: dict) -> None:
    """写工具（异步接口）成功后：注册对象状态轮询，完成时按 Plan 推进下一步。
    tracker 走闭包注入（不进 checkpoint state，避免 msgpack 序列化不可序列化对象）。"""
    if tool is None or not (tool.is_write and tracker and ctx.conversation_id):
        return
    if not result or result.get("status") not in (200, 201, 202):
        return
    object_id = _extract_object_id(result.get("body"))
    if not object_id:
        return
    mapping = WRITE_TRACK_MAP.get(tool.name)
    if not mapping:
        return
    query_tool, object_type, id_param = mapping
    # 定位所属 plan（execute 轮优先按 suggestion 反查；兜底取会话活跃 plan）
    plan_id = ""
    suggestion_id = str(ctx.suggestion_id) if ctx.suggestion_id else ""
    if store is not None and store.enabled:
        try:
            if suggestion_id:
                sug = await store.get_suggestion(suggestion_id)
                plan_id = (sug or {}).get("plan_id") or ""
            if not plan_id:
                plan = await store.get_active_plan(ctx.conversation_id)
                plan_id = (plan or {}).get("plan_id") or ""
        except Exception as e:  # noqa: BLE001
            log.warning("plan lookup for monitor failed: %s", e)
    tracker.register(
        object_type=object_type, object_id=object_id,
        conversation_id=ctx.conversation_id, task_id=ctx.task_id,
        task_token=ctx.task_token, query_tool=query_tool,
        query_args={id_param: object_id},
        plan_id=plan_id, suggestion_id=suggestion_id,
        action_type=tool.name, target_status="SUCCEEDED")


class AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    ctx: TaskContext
    pending_tools: Optional[list[dict]]  # 本轮的待执行工具调用（原生 tool_calls: [{id,name,args}]）


def _chunk_text(chunk: Any) -> str:
    """chunk.content 可能是 str（文本）或 list（content blocks），统一为 str。"""
    content = getattr(chunk, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def _chunk_reasoning(chunk: Any) -> str:
    """取 chunk 上的推理链增量（DeepSeek V4：additional_kwargs 或 response_metadata）。"""
    for src in (getattr(chunk, "additional_kwargs", None) or {},
                getattr(chunk, "response_metadata", None) or {}):
        rc = src.get("reasoning_content") or src.get("reasoning")
        if rc:
            return rc if isinstance(rc, str) else str(rc)
    return ""


class EventBatcher:
    """thinking/delta 增量聚合：累计到阈值再发，降低 gRPC/SSE 帧数（聚合降帧）。

    现状每 token 一次 send_event → admin 每事件一次转发/落库；聚合后帧数降一个数量级，
    前端观感仍"接近实时"（40 字符内即时可见，长段不超过一个推理片段）。
    """

    FLUSH_CHARS = 40

    def __init__(self, client: GrpcClient, task_id: str) -> None:
        self.client = client
        self.task_id = task_id
        self._buf: dict[str, list[str]] = {"thinking": [], "delta": []}

    async def add(self, kind: str, text: str) -> None:
        buf = self._buf[kind]
        buf.append(text)
        if sum(len(x) for x in buf) >= self.FLUSH_CHARS:
            await self.flush(kind)

    async def flush(self, kind: Optional[str] = None) -> None:
        kinds = ["thinking", "delta"] if kind is None else [kind]
        for k in kinds:
            buf = self._buf[k]
            if not buf:
                continue
            text = "".join(buf)
            buf.clear()
            await self.client.send_event(self.task_id, k, text)


def _openai_function(name: str, description: str, parameters: dict) -> dict:
    """构造 OpenAI function calling 格式的工具条目。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def approve_tool_name(write_tool_name: str) -> str:
    """写工具名 -> 审批工具名：training_create -> approve_training_create。"""
    return f"approve_{write_tool_name}"


def action_type_from_approve(name: str) -> str:
    """审批工具名 -> 写工具名（action_type）：approve_training_create -> training_create。"""
    return name[len("approve_"):] if name.startswith("approve_") else name


# 内置工具（本地 handler 直写库）的 OpenAI function schema
BUILTIN_TOOL_SCHEMAS: dict[str, dict] = {
    "plan_create": _openai_function(
        "plan_create",
        "记录多步骤任务的执行计划（步骤清单与顺序）。只建立规划记录，不产生任何审批建议。"
        "任务包含多个步骤（如「训练并部署」）时先调用本工具。",
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "计划摘要，如 训练并部署 LSTM 模型"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action_type": {"type": "string", "description": "写操作名，如 training_create/serving_deploy"},
                            "target_type": {"type": "string"},
                            "target_id": {"type": "integer", "description": "目标 ID；暂未知填 0"},
                            "params": {"type": "object", "description": "业务参数（可选）"},
                            "reason": {"type": "string"},
                            "priority": {"type": "string", "enum": ["HIGH", "NORMAL", "LOW"]},
                        },
                        "required": ["action_type"],
                    },
                },
            },
            "required": ["summary", "steps"],
        },
    ),
    "plan_update": _openai_function(
        "plan_update",
        "更新计划状态：可更新计划整体状态（DONE/FAILED/CANCELLED），"
        "或计划中某一步骤的状态（done/failed/cancelled，附说明）。",
        {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "plan_create 返回的 plan_id"},
                "step_no": {"type": "integer", "description": "要更新的步骤号（可选）"},
                "step_status": {"type": "string", "enum": ["done", "failed", "cancelled"],
                                "description": "步骤状态（与 step_no 一起用）"},
                "status": {"type": "string", "enum": ["DONE", "FAILED", "CANCELLED"],
                           "description": "plan 整体状态（不传 step_no 时用）"},
                "note": {"type": "string", "description": "变更说明（展示给用户）"},
            },
            "required": ["plan_id"],
        },
    ),
}

# 追加到每个 approve_<写工具> 的审批上下文参数（plan 步骤推进 / 决策轮重试 / 目标定位）
_APPROVE_EXTRA_PROPERTIES: dict[str, dict] = {
    "plan_id": {"type": "string", "description": "所属计划 plan_id（可选，plan 步骤推进时填）"},
    "step_no": {"type": "integer", "description": "计划内步骤号（可选）"},
    "retry_of": {"type": "string", "description": "重试某条已失败建议的 suggestion_id（可选）"},
    "target_type": {"type": "string",
                    "enum": ["dataset", "training_job", "model_version", "serving_endpoint"],
                    "description": "操作目标类型（模型从上下文携带，如对数据集发起训练填 dataset）"},
    "target_id": {"type": "integer", "description": "操作目标对象 ID（与 target_type 配套）"},
}

# approve_* 的审批上下文键：不进业务 params，单独落建议行（plan 关联 / 目标定位 / 重试）
_APPROVE_CONTEXT_KEYS = {"plan_id", "step_no", "retry_of", "target_type", "target_id"}


def _build_approve_schema(write_tool: agent_pb2.ToolSchema) -> dict:
    """写工具 -> approve_<写工具> 审批工具 schema。

    parameters 复用写工具本体 schema（模型填业务参数零转换），
    追加 plan_id/step_no/retry_of 审批上下文参数；描述声明只落审批建议不直接执行。
    """
    try:
        params = json.loads(write_tool.parameters or "{}")
    except json.JSONDecodeError:
        params = {"type": "object", "properties": {}}
    props = dict(params.get("properties") or {})
    props.update(_APPROVE_EXTRA_PROPERTIES)
    merged = {
        "type": "object",
        "properties": props,
        "required": params.get("required") or [],
    }
    name = approve_tool_name(write_tool.name)
    return _openai_function(
        name,
        f"提出「{write_tool.description}」的处置建议（不直接执行）："
        f"填写操作参数后生成待审批建议，经人工确认后由系统自动执行。",
        merged,
    )


def build_openai_tools(registry: ToolRegistry) -> list[dict]:
    """bind_tools 用的完整工具列表（每轮按注册表重建，更新即生效）。

    = registry 只读工具（is_write 过滤，模型可直接调用查询真实状态）
    + plan_create / plan_update（本地 handler）
    + approve_<写工具>（每个写操作一个审批工具；写工具本体**不进 tools**，模型无执行路径）
    """
    tools: list[dict] = []
    for t in registry.all():
        if t.is_write:
            tools.append(_build_approve_schema(t))
        else:
            try:
                params = json.loads(t.parameters or "{}")
            except json.JSONDecodeError:
                params = {"type": "object", "properties": {}}
            tools.append(_openai_function(t.name, t.description, params))
    tools.append(BUILTIN_TOOL_SCHEMAS["plan_create"])
    tools.append(BUILTIN_TOOL_SCHEMAS["plan_update"])
    return tools


def _collect_business_params(args: dict) -> dict:
    """approve_* 场景：写工具业务参数在 args 顶层（如 {"datasetId": 3}），
    不在 params 键里 —— 收集除审批上下文键外的顶层键作为 params。
    兼容旧式 suggest_action 显式传 params 键的情况。"""
    explicit = args.get("params")
    if isinstance(explicit, dict) and explicit:
        return explicit
    return {k: v for k, v in args.items()
            if k not in _APPROVE_CONTEXT_KEYS and v is not None}


def _missing_required_params(write_tool: Optional[agent_pb2.ToolSchema], args: dict) -> list[str]:
    """approve_<写工具> 校验：写工具 schema 的 required 业务参数是否齐全（顶层或 params 内）。"""
    if write_tool is None:
        return []
    try:
        schema = json.loads(write_tool.parameters or "{}")
    except json.JSONDecodeError:
        return []
    required = schema.get("required") or []
    if not required:
        return []
    merged = dict(args)
    if isinstance(args.get("params"), dict):
        merged.update(args["params"])
    return [k for k in required if merged.get(k) is None]


async def handle_suggest_action(store: Any, ctx: TaskContext, args: dict,
                                action_type: str = "") -> dict:
    """落一条 PENDING 写操作建议（系统参数注入）。

    LLM 只填业务参数；conversation_id/source_task_id 由本层注入；
    可挂 plan_id + step_no（plan 的步骤建议）与 retry_of（决策轮重试）。
    action_type 由审批工具名推导（approve_training_create -> training_create），
    兼容旧调用从 args 读。
    """
    if store is None or not store.enabled:
        log.warning("suggest_action unavailable: agent DB disabled")
        return {"status": 500, "body": "suggest_action unavailable (agent DB disabled)"}
    action = action_type or str(args.get("action_type", ""))
    if not action:
        return {"status": 400, "body": "action_type is required"}
    try:
        sid = await store.insert_suggestion({
            "source_task_id": ctx.task_id,
            "conversation_id": ctx.conversation_id,
            "plan_id": str(args.get("plan_id", "")),
            "step_no": int(args.get("step_no", 0) or 0),
            "retry_of": str(args.get("retry_of", "")),
            "action_type": action,
            "target_type": str(args.get("target_type", "")),
            "target_id": int(args.get("target_id", 0) or 0),
            "params": _collect_business_params(args),
            "reason": str(args.get("reason", "")),
            "priority": str(args.get("priority", "NORMAL")),
        })
    except Exception as e:  # noqa: BLE001
        log.warning("suggest_action persist failed: %s", e)
        return {"status": 500, "body": f"suggestion create failed: {e}"}
    body = json.dumps({"suggestion_id": sid}, ensure_ascii=False)
    log.info("suggestion suggested: %s action=%s", sid[:8], action)
    return {"status": 200, "body": body}


async def handle_plan_create(store: Any, ctx: TaskContext, args: dict) -> dict:
    """plan_create 内置工具：只建规划备忘录（summary + steps 清单），零建议副作用。

    步骤审批由模型后续逐步用 approve_<写操作名>(plan_id, step_no) 提出；
    conversation_id 由本层注入，steps 补 step_no/status 后存 plan.steps（模型掌舵状态）。
    """
    if store is None or not store.enabled:
        log.warning("plan_create unavailable: agent DB disabled")
        return {"status": 500, "body": "plan_create unavailable (agent DB disabled)"}
    raw_steps = args.get("steps") or []
    steps: list[dict] = []
    for idx, s in enumerate(raw_steps):
        if not isinstance(s, dict) or not s.get("action_type"):
            continue
        step = dict(s)
        step["step_no"] = idx + 1
        step.setdefault("status", "pending")  # pending/executing/done/failed/cancelled
        step.setdefault("note", "")
        steps.append(step)
    plan = {"conversation_id": ctx.conversation_id,
            "summary": str(args.get("summary", "")),
            "status": "RUNNING", "steps": steps}
    try:
        await store.upsert_plan(plan)
    except Exception as e:  # noqa: BLE001
        log.warning("plan_create upsert failed: %s", e)
        return {"status": 500, "body": f"plan create failed: {e}"}
    plan_id = plan.get("plan_id") or ""
    body = json.dumps({
        "plan_id": plan_id,
        "steps": len(steps),
        "instruction": f"规划已建立（{len(steps)} 个步骤）。步骤按顺序处理："
                       f"用 approve_<写操作名>(plan_id={plan_id}, step_no=N) 逐步提出审批建议，上一步完成后再提下一步。",
    }, ensure_ascii=False)
    log.info("plan created: %s steps=%d", plan_id[:8], len(steps))
    return {"status": 200, "body": body}


async def handle_plan_update(store: Any, ctx: TaskContext, args: dict,
                             notify: Any = None) -> dict:
    """plan_update 内置工具：模型掌舵 plan 生命周期。

    - plan 级：args.status ∈ DONE/FAILED/CANCELLED（结束整条计划）
    - 步骤级：args.step_no + args.status ∈ done/failed/cancelled（更新 plan.steps 中某步）
    - args.note 可选说明（前端展示）
    变更经 plan_update 事件通知前端（notify 为 tracker 的更新+通知回调）。
    """
    if store is None or not store.enabled:
        log.warning("plan_update unavailable: agent DB disabled")
        return {"status": 500, "body": "plan_update unavailable (agent DB disabled)"}
    plan_id = str(args.get("plan_id", ""))
    if not plan_id:
        return {"status": 400, "body": "plan_id is required"}
    note = str(args.get("note", ""))
    plan_status = str(args.get("status", "")).upper()
    step_no = int(args.get("step_no", 0) or 0)
    step_status = str(args.get("step_status", "")).lower()

    if step_no > 0:
        if step_status not in ("done", "failed", "cancelled"):
            return {"status": 400, "body": f"invalid step_status: {step_status}"}
        try:
            await store.update_plan_step(plan_id, step_no, step_status, note)
        except Exception as e:  # noqa: BLE001
            log.warning("plan step update failed: %s", e)
            return {"status": 500, "body": f"plan step update failed: {e}"}
        message = f"步骤 {step_no} 已标记为 {step_status}" + (f"：{note}" if note else "")
        if notify is not None:
            await notify(plan_id, "RUNNING", message)
        return {"status": 200, "body": json.dumps(
            {"plan_id": plan_id, "step_no": step_no, "step_status": step_status},
            ensure_ascii=False)}

    if plan_status not in ("DONE", "FAILED", "CANCELLED"):
        return {"status": 400, "body": f"invalid plan status: {plan_status}"}
    try:
        await store.update_plan_status(plan_id, plan_status)
    except Exception as e:  # noqa: BLE001
        log.warning("plan status update failed: %s", e)
        return {"status": 500, "body": f"plan status update failed: {e}"}
    if notify is not None:
        await notify(plan_id, plan_status, note or f"计划已{plan_status}")
    return {"status": 200, "body": json.dumps(
        {"plan_id": plan_id, "status": plan_status}, ensure_ascii=False)}


def build_graph(llm_runtime: Any, http: AdminHttpClient,
                registry: ToolRegistry, client: GrpcClient,
                tracker: Any = None, store: Any = None) -> Any:
    """构建并编译决策图。llm_runtime/http/registry/client/tracker/store 为进程级共享实例（闭包），ctx 走 state。
    tracker/store 不进 state（msgpack 不可序列化），只读工具成功回调用 tracker 注册异步跟踪。"""

    # plan_update 的变更通知：tracker.notify_plan 仅做 plan_update 上报（状态已由 handler 落库）
    tracker_notify = getattr(tracker, "notify_plan", None)

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """决策节点：bind_tools 注入原生工具后流式调用 LLM。

        流式策略：推理链增量实时发 thinking；正文增量实时发 delta（工具轮 content 通常为空，
        出现 tool_call_chunks 即视为工具轮，不再展示正文）；收尾按 merged.tool_calls 分流。
        assistant 消息保留 reasoning_content 原样回传（V4 带 tools 轮次硬要求，缺失 400）。
        事件经 EventBatcher 聚合（40 字符 flush）降低帧数。
        """
        ctx: TaskContext = state["ctx"]
        llm = llm_runtime.select(ctx.reasoning_enabled).bind_tools(build_openai_tools(registry))
        messages = list(state["messages"])
        chunks: list[Any] = []
        reasoning_parts: list[str] = []
        saw_tool_chunks = False
        batcher = EventBatcher(client, ctx.task_id)
        async for chunk in llm.astream(messages):
            chunks.append(chunk)
            rc = _chunk_reasoning(chunk)
            if rc:
                reasoning_parts.append(rc)
                await batcher.add("thinking", rc)
            if getattr(chunk, "tool_call_chunks", None):
                saw_tool_chunks = True
            text = _chunk_text(chunk)
            if text and not saw_tool_chunks:
                await batcher.add("delta", text)
        await batcher.flush()  # 收尾：把残余增量发完
        if not chunks:
            return {"messages": [AIMessage(content="")], "pending_tools": []}
        merged = chunks[0]
        for c in chunks[1:]:
            merged = merged + c
        # 聚合推理链全文挂到 additional_kwargs，供 TaskResult.reasoning 落库/展示 + 下轮原样回传
        if reasoning_parts:
            merged.additional_kwargs["reasoning_content"] = "".join(reasoning_parts)

        tool_calls = getattr(merged, "tool_calls", None) or []
        if tool_calls:
            # 原生 tool_calls: [{id, name, args}] -> pending_tools（tools_node 执行）
            pending = [{
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "name": tc.get("name") or "",
                "args": tc.get("args") or {},
            } for tc in tool_calls if tc.get("name")]
            return {"messages": [merged], "pending_tools": pending}
        return {"messages": [merged], "pending_tools": []}

    async def tools_node(state: AgentState) -> dict[str, Any]:
        """工具节点：执行 pending_tools，结果以原生 ToolMessage 回填（role=tool, tool_call_id 关联）。

        分发：plan_create/plan_update 走本地 handler；approve_<写工具> 走审批落库
        （只生成 PENDING 建议，模型永远无法直接执行写操作）；其余只读工具走 admin HTTP。
        """
        ctx: TaskContext = state["ctx"]
        tool_msgs: list[Any] = []
        for tc in (state.get("pending_tools") or []):
            name = tc["name"]
            args = tc.get("args") or {}
            call_id = tc.get("id") or ""
            await client.send_event(ctx.task_id, "tool_call",
                                    json.dumps({"name": name, "args": args}, ensure_ascii=False))
            if name == "plan_create":
                result = await handle_plan_create(store, ctx, args) if store is not None else {
                    "status": 500, "body": "plan_create unavailable (agent DB disabled)"}
            elif name == "plan_update":
                result = await handle_plan_update(store, ctx, args, notify=tracker_notify) \
                    if store is not None else {"status": 500,
                                               "body": "plan_update unavailable (agent DB disabled)"}
            elif name.startswith("approve_"):
                # 审批工具：落 PENDING 建议，action_type 由工具名推导（写工具本体绝不在本节点执行）
                action = action_type_from_approve(name)
                write_tool = registry.get(action)
                missing = _missing_required_params(write_tool, args) if write_tool else []
                if missing:
                    # 缺必填业务参数：返回 400 提示，模型会重新调用补齐（而不是落空参数建议导致 execute 400）
                    result = {"status": 400,
                              "body": f"{name} 缺少必填参数: {missing}，请补齐后重新调用"}
                else:
                    result = await handle_suggest_action(store, ctx, args, action_type=action) \
                        if store is not None else {"status": 500,
                                                   "body": f"{name} unavailable (agent DB disabled)"}
            else:
                tool = registry.get(name)
                if tool is None:
                    result = {"status": 0, "body": f"unknown tool: {name}"}
                else:
                    result = await http.call(tool, args, ctx)
                    await _maybe_register_tracker(tracker, store, ctx, tool, result)
            body = result.get("body") if isinstance(result, dict) else result
            summary = str(body)[:500] if body is not None else ""
            await client.send_event(ctx.task_id, "tool_result",
                                    json.dumps({"name": name, "summary": summary}, ensure_ascii=False))
            tool_msgs.append(ToolMessage(
                content=json.dumps(result, ensure_ascii=False), tool_call_id=call_id))
        return {"messages": tool_msgs, "pending_tools": []}

    def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        if state.get("pending_tools"):
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue,
                                {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=MemorySaver())


async def run_graph(graph: Any, ctx: TaskContext,
                    messages: list[Any], max_rounds: int = 10) -> list[Any]:
    """执行图，返回收敛后的完整消息列表。

    达到 recursion 上限（LLM 持续调工具）时抛 GraphRecursionError —— 从 checkpoint
    快照取已产生的消息，保证"轮数耗尽也能正常收敛产出结论"。
    """
    config = {
        "configurable": {"thread_id": ctx.task_id},
        "recursion_limit": max_rounds * 4 + 16,
    }
    try:
        result = await graph.ainvoke({"messages": messages, "ctx": ctx, "pending_tools": []}, config=config)
        return result["messages"]
    except GraphRecursionError:
        log.warning("graph recursion limit reached: task=%s", ctx.task_id)
        snapshot = await graph.aget_state(config)
        return (snapshot.values or {}).get("messages") or messages
