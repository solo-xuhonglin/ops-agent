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
import asyncio
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

# wait_until 轮询参数
WAIT_POLL_INTERVAL_S = 4.0
WAIT_MAX_CONSECUTIVE_FAILS = 3
WAIT_TERMINAL_STATUSES = {"FAILED", "CANCELLED", "STOPPED"}
# wait_until 的 query_tool → 对象 ID 参数名（admin 查询 API 的 path 模板变量）
QUERY_TOOL_ID_ARG: dict[str, str] = {
    "training_get": "jobId",
    "serving_get": "endpointId",
    "dataset_get": "datasetId",
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
    "wait_until": _openai_function(
        "wait_until",
        "等待异步对象状态到达目标/发生变化，带超时与提前返回。系统代为循环查询，"
        "对象状态变化、updated_at 更新、到达 target_status 或进入终态时立即返回最新状态；"
        "wait_seconds 内无变化则返回当前最新状态（仍在进行中）。提交异步操作（训练/部署）后等待完成时使用。",
        {
            "type": "object",
            "properties": {
                "query_tool": {"type": "string", "enum": ["training_get", "serving_get", "dataset_get"],
                               "description": "要等待的只读查询工具名"},
                "object_id": {"type": "integer",
                              "description": "要等待的对象 ID（训练任务 jobId / 服务端点 endpointId / 数据集 datasetId，由 query_tool 决定）"},
                "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60,
                                 "description": "最多等待秒数；超时返回当前状态并标记仍在进行中"},
                "target_status": {"type": "string", "description": "期望状态（可选），如 SUCCEEDED；不填则等任意变化"},
            },
            "required": ["query_tool"],
        },
    ),
    "sleep": _openai_function(
        "sleep",
        "在当前任务内纯等待 N 秒（不查询任何对象，仅放慢决策节奏）。"
        "用于限流/给后端操作留时间/冷却等场景；不要用于等待异步对象状态变化（应改用 wait_until）。"
        "等待期间发 progress 事件；单次上限 300 秒，避免长挂。",
        {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "minimum": 1, "maximum": 300, "default": 10,
                            "description": "休眠秒数"},
            },
            "required": ["seconds"],
        },
    ),
}

# 追加到每个 approve_<写工具> 的审批上下文参数（plan 步骤推进 / 决策轮重试 / 目标定位 / 理由）
_APPROVE_EXTRA_PROPERTIES: dict[str, dict] = {
    "plan_id": {"type": "string", "description": "所属计划 plan_id（可选，plan 步骤推进时填）"},
    "step_no": {"type": "integer", "description": "计划内步骤号（可选）"},
    "retry_of": {"type": "string", "description": "重试某条已失败建议的 suggestion_id（可选）"},
    "target_type": {"type": "string",
                    "enum": ["dataset", "training_job", "model_version", "serving_endpoint"],
                    "description": "操作目标类型（模型从上下文携带，如对数据集发起训练填 dataset）"},
    "target_id": {"type": "integer", "description": "操作目标对象 ID（与 target_type 配套）"},
    "reason": {"type": "string",
               "description": "建议理由/操作说明，告诉用户为什么要执行该操作（建议填写，展示在确认卡片）"},
}

# approve_* 的审批上下文键：不进业务 params，单独落建议行（plan 关联 / 目标定位 / 重试 / 理由）
_APPROVE_CONTEXT_KEYS = {"plan_id", "step_no", "retry_of", "target_type", "target_id", "reason"}


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
                                action_type: str = "",
                                client: Optional[GrpcClient] = None) -> dict:
    """落一条 PENDING 写操作建议（系统参数注入）。

    LLM 只填业务参数；conversation_id/source_task_id 由本层注入；
    可挂 plan_id + step_no（plan 的步骤建议）与 retry_of（决策轮重试）。
    action_type 由审批工具名推导（approve_training_create -> training_create），
    兼容旧调用从 args 读。

    落库成功后立即推 `suggestion_created` SSE 事件，让前端用真实 suggestionId
    渲染 APPROVAL 时间线行——避免前端必须等 fetchSuggestions 才能看到卡
    （修复"授权卡要重新进入会话才出现"的体验断裂）。

    去重：insert_suggestion 幂等（自然键命中开放态同款则复用，见 TaskStore.find_open_duplicate）。
    命中时不推 suggestion_created（卡已存在），并在 tool 返回体里用自然语言提示模型收敛，
    避免它继续刷同一条申请。approve_* 只经本函数落库，所以这一处兼顾"硬兜底"与"教模型"。
    """
    if store is None or not store.enabled:
        log.warning("suggest_action unavailable: agent DB disabled")
        return {"status": 500, "body": "suggest_action unavailable (agent DB disabled)"}
    action = action_type or str(args.get("action_type", ""))
    if not action:
        return {"status": 400, "body": "action_type is required"}
    try:
        sid, created = await store.insert_suggestion({
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
    if not created:
        # 去重命中：同款申请仍在等待审批/执行，卡已经在界面上——不重复推事件、不重复落库，
        # 只把"别再提"讲清楚，让模型转去等待或推进下一步。
        log.info("suggestion duplicate suppressed: %s action=%s", sid[:8], action)
        return {"status": 200, "body": json.dumps({
            "suggestion_id": sid,
            "duplicate": True,
            "note": (f"该 {action} 申请已存在（suggestion_id={sid}），"
                     "正在等待审批或执行中，请勿重复提交。"
                     "请等待审批结果。"),
        }, ensure_ascii=False)}
    # 推 suggestion_created：前端收到后立即 upsert APPROVAL 行（独立事件，不在 tool_result 兜底）
    if client is not None:
        try:
            await client.send_event(ctx.task_id, "suggestion_created",
                                    json.dumps({
                                        "suggestionId": sid,
                                        "actionType": action,
                                        "targetType": str(args.get("target_type", "")),
                                        "targetId": int(args.get("target_id", 0) or 0),
                                        "params": _collect_business_params(args),
                                        "reason": str(args.get("reason", "")),
                                        "priority": str(args.get("priority", "NORMAL")),
                                        "planId": str(args.get("plan_id", "")),
                                        "stepNo": int(args.get("step_no", 0) or 0),
                                        "retryOf": str(args.get("retry_of", "")),
                                    }, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            log.warning("suggestion_created event failed (non-blocking): %s", e)
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


def _extract_wait_fields(result: dict) -> tuple[Optional[str], str]:
    """从查询结果提取 (status, updated_at)；解析失败返回 (None, '')。

    status 统一大写便于比较；updated_at 兼容 updatedAt/updatedTime 命名，缺失则为空串
    （此时 updated_at 提前返回条件自动退化，只按 status 判定）。
    """
    body = result.get("body")
    if not isinstance(body, str):
        return None, ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, ""
    node = data.get("data") if isinstance(data, dict) else None
    if isinstance(node, dict):
        status = node.get("status")
        updated = (node.get("updated_at") or node.get("updatedAt")
                   or node.get("updatedTime") or "")
        return (str(status).upper() if status else None), str(updated)
    if isinstance(node, list) and node:
        status = node[0].get("status")
        updated = (node[0].get("updated_at") or node[0].get("updatedAt")
                   or node[0].get("updatedTime") or "")
        return (str(status).upper() if status else None), str(updated)
    return None, ""


def _wait_result(result: dict, status: Optional[str], updated_at: str,
                 still_in_progress: bool = False) -> dict:
    """wait_until 返回：原始查询结果 + 提取的状态字段（避免模型二次猜测）。"""
    try:
        parsed = json.loads(result.get("body") or "{}")
    except json.JSONDecodeError:
        parsed = {"raw": result.get("body")}
    body = json.dumps({
        "data": parsed,
        "status": status,
        "updated_at": updated_at,
        "_still_in_progress": still_in_progress,
    }, ensure_ascii=False)
    return {"status": result.get("status"), "body": body}


async def handle_wait_until(registry: Any, http: AdminHttpClient, client: GrpcClient,
                            ctx: TaskContext, args: dict) -> dict:
    """wait_until 内置工具：循环查询直至 目标/终态/updated_at 变化/超时，带提前返回。

    - 提前返回：命中 target_status / 终态集合（FAILED/CANCELLED/STOPPED）/ updated_at 变化；
    - 超时返回：wait_seconds 用尽 → 返回当前最新状态 + still_in_progress=true；
    - 连续查询失败 WAIT_MAX_CONSECUTIVE_FAILS 次 → 提前返回错误（避免空转）；
    - 等待期间发 progress 事件（前端时间线可见，不干等）。
    """
    query_tool = str(args.get("query_tool", ""))
    tool = registry.get(query_tool) if registry is not None else None
    if tool is None:
        return {"status": 0, "body": f"unknown query_tool: {query_tool}"}
    id_arg = QUERY_TOOL_ID_ARG.get(query_tool)
    object_id = args.get("object_id")
    if id_arg is None or object_id is None:
        return {"status": 400,
                "body": f"object_id is required for {query_tool} (e.g. jobId/endpointId/datasetId)"}
    query_args = {id_arg: int(object_id)}
    raw_wait = args.get("wait_seconds")
    wait_seconds = max(0, min(int(raw_wait if raw_wait is not None else 60), 120))
    target_status = str(args.get("target_status", "")).upper()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + wait_seconds
    last_status = ""
    last_updated_at = ""
    fail_count = 0
    last_result: dict = {"status": 0, "body": ""}
    while True:
        last_result = await http.call(tool, query_args, ctx)
        status, updated_at = _extract_wait_fields(last_result)
        if status is None:
            fail_count += 1
            if fail_count >= WAIT_MAX_CONSECUTIVE_FAILS:
                log.warning("wait_until query failed %d times: %s", fail_count, query_tool)
                return _wait_result(last_result, None, "", still_in_progress=False)
        else:
            fail_count = 0
            if ctx.task_id:
                await client.send_event(ctx.task_id, "progress",
                                        f"等待 {query_tool} 完成，当前状态 {status}")
            # 提前返回①：到达目标状态
            if target_status and status == target_status:
                return _wait_result(last_result, status, updated_at)
            # 提前返回②：进入终态（失败也要让 agent 看到，而非等到超时）
            if status in WAIT_TERMINAL_STATUSES:
                return _wait_result(last_result, status, updated_at)
            # 提前返回③：数据有更新（updated_at 变化，状态可能未变但细节在推进）
            if updated_at and last_updated_at and updated_at != last_updated_at:
                return _wait_result(last_result, status, updated_at)
            last_status = status
            if updated_at:
                last_updated_at = updated_at
        if loop.time() >= deadline:
            return _wait_result(last_result, status or last_status,
                                last_updated_at, still_in_progress=True)
        await asyncio.sleep(WAIT_POLL_INTERVAL_S)


def _format_plan_summary(plan: Optional[dict]) -> str:
    """plan dict → 决策上下文文本（execute 内闭环 / 推进轮复用）。"""
    if not plan:
        return ""
    steps = plan.get("steps") or []
    step_lines = "\n".join(
        f"  step{s.get('step_no')}: {s.get('action_type')} (target={s.get('target_type')}/"
        f"{s.get('target_id')}) status={s.get('status', 'pending')}"
        + (f" note={s.get('note')}" if s.get("note") else "")
        for s in steps)
    return (f"plan_id={plan.get('plan_id')}\nsummary={plan.get('summary')}\n"
            f"status={plan.get('status')}\nsteps:\n{step_lines or '  （空）'}")


def _extract_conclusion(messages: list) -> str:
    """取最后一条 assistant 消息（有内容且非工具调用轮才用），否则提示未收敛。"""
    for m in reversed(messages):
        if getattr(m, "type", "") == "ai" and getattr(m, "content", None):
            if getattr(m, "tool_calls", None):
                continue  # 工具调用轮（原生 tool_calls），内容非结论
            return m.content
    return "no conclusion produced (max tool rounds reached)"


def _find_recent_repeat_read(messages: list, name: str, args_hash: str) -> bool:
    """检查当前轮之前的 AI 消息是否已以相同 name+args 调过只读工具——防 LLM 反复调查询。

    跳过列表末尾的当前轮 AI 消息（agent_node 刚 append 的），仅在更早的历史里查重。
    只查只读工具（registry.is_write=False）；写工具/审批工具/内置工具由其他机制保护。
    """
    seen_current = False
    for m in reversed(messages[-6:]):
        if getattr(m, "type", "") != "ai":
            continue
        if not seen_current:
            seen_current = True
            continue  # 当前轮，跳过
        for tc in (getattr(m, "tool_calls", None) or []):
            tname = tc.get("name") or ""
            targs = json.dumps(tc.get("args") or {}, sort_keys=True, ensure_ascii=False)
            if tname == name and targs == args_hash:
                return True
    return False


async def handle_sleep(client: GrpcClient, ctx: TaskContext, args: dict) -> dict:
    """sleep 内置工具：在当前任务内纯等待 N 秒（无查询），用于限流/冷却。

    返回 `{status:200, body: "slept N seconds"}`；agent 可在下一轮继续决策。
    """
    seconds = max(1, min(int(args.get("seconds", 10) or 10), 300))
    if ctx.task_id:
        await client.send_event(ctx.task_id, "progress", f"sleep {seconds}s ...")
    await asyncio.sleep(seconds)
    return {"status": 200, "body": json.dumps(
        {"slept_seconds": seconds, "message": f"已休眠 {seconds} 秒，请继续决策"}, ensure_ascii=False)}


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
                                    json.dumps({"id": call_id, "name": name, "args": args},
                                               ensure_ascii=False))
            if name == "plan_create":
                result = await handle_plan_create(store, ctx, args) if store is not None else {
                    "status": 500, "body": "plan_create unavailable (agent DB disabled)"}
            elif name == "plan_update":
                result = await handle_plan_update(store, ctx, args, notify=tracker_notify) \
                    if store is not None else {"status": 500,
                                               "body": "plan_update unavailable (agent DB disabled)"}
            elif name == "wait_until":
                # 长查询：超时 + 提前返回（agent 自主轮询异步对象状态，无需外部 continue）
                result = await handle_wait_until(registry, http, client, ctx, args)
            elif name == "sleep":
                # 纯等待 N 秒（限流/冷却；非等待异步对象）
                result = await handle_sleep(client, ctx, args)
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
                    result = await handle_suggest_action(store, ctx, args,
                                                        action_type=action, client=client) \
                        if store is not None else {"status": 500,
                                                   "body": f"{name} unavailable (agent DB disabled)"}
            else:
                tool = registry.get(name)
                if tool is None:
                    result = {"status": 0, "body": f"unknown tool: {name}"}
                else:
                    # 只读工具重复检测：避免 LLM 反复调同一查询而不切换到 wait_until
                    if not tool.is_write and _find_recent_repeat_read(
                            state.get("messages") or [], name,
                            json.dumps(args, sort_keys=True, ensure_ascii=False)):
                        result = {
                            "status": 400,
                            "body": (f"已检测到对 {name} 的重复调用（相同参数）。"
                                     f"请改用 wait_until(query_tool='{name}', object_id, "
                                     f"wait_seconds=60~120, target_status='SUCCEEDED') "
                                     "由系统代为轮询；或调 sleep(seconds) 限流等待。"),
                        }
                    else:
                        result = await http.call(tool, args, ctx)
                        await _maybe_register_tracker(tracker, store, ctx, tool, result)
            body = result.get("body") if isinstance(result, dict) else result
            summary = str(body)[:500] if body is not None else ""
            await client.send_event(ctx.task_id, "tool_result",
                                    json.dumps({"id": call_id, "name": name, "summary": summary},
                                               ensure_ascii=False))
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
                    messages: list[Any], max_rounds: int = 10) -> tuple[list[Any], bool]:
    """执行图，返回 (收敛后的完整消息列表, 是否触发 recursion_limit)。

    达到 recursion 上限（LLM 持续调工具）时抛 GraphRecursionError —— 从 checkpoint
    快照取已产生的消息；调用方根据 hit_recursion_limit 决定是否在结论前缀注明「任务停止」。
    """
    config = {
        "configurable": {"thread_id": ctx.task_id},
        "recursion_limit": max_rounds * 4 + 16,
    }
    try:
        result = await graph.ainvoke({"messages": messages, "ctx": ctx, "pending_tools": []}, config=config)
        return result["messages"], False
    except GraphRecursionError:
        log.warning("graph recursion limit reached: task=%s", ctx.task_id)
        snapshot = await graph.aget_state(config)
        return (snapshot.values or {}).get("messages") or messages, True
