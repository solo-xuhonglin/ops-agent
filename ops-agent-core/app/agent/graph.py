"""LangGraph 决策图（M3.5+，标准 LangChain 生态）。

StateGraph: START -> agent(LLM 决策) -> tools(执行工具) -> 有工具调用回 agent / 否则 END
消息体系为 langchain BaseMessage + add_messages reducer（checkpoint 持久化原生兼容）；
LLM 用 langchain_openai.ChatOpenAI，**不依赖原生 function calling** —— 工具清单以
SystemMessage 注入 prompt，模型按「输出契约」返回 JSON 工具调用（deepseek-reasoner
等不支持 tools 参数的推理模型也可用）；工具调用放 state.pending_tools 传递，
回填 LLM 的消息流只含普通文本（避免 tool/tool_calls 消息导致 reasoner 类模型 400）。

任务上下文（TaskContext）放在 state：同一图实例可被多任务（不同 thread_id）并发 ainvoke。
对外契约：调用方（core.handle_dispatch）仍收 TaskEvent / TaskResult；
agent 节点流式产出（astream）：推理链增量发 thinking、正文增量发 delta（工具 JSON 不展示，
收尾按是否解析出工具调用分流），聚合推理链挂最终 AIMessage.additional_kwargs["reasoning_content"]。
"""
import json
import logging
import re
import uuid
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, SystemMessage
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


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    ctx: TaskContext
    pending_tools: Optional[list[dict]]  # 本轮的待执行工具调用（JSON 契约解析结果）


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
    """取 chunk 上的推理链增量（DeepSeek reasoner：additional_kwargs 或 response_metadata）。"""
    for src in (getattr(chunk, "additional_kwargs", None) or {},
                getattr(chunk, "response_metadata", None) or {}):
        rc = src.get("reasoning_content") or src.get("reasoning")
        if rc:
            return rc if isinstance(rc, str) else str(rc)
    return ""


def _looks_like_json(text: str) -> bool:
    """判断流式累积文本是否以 JSON 起始（工具调用轮的特征开头）。"""
    t = text.lstrip()
    return t.startswith(("{", "[", "```"))


def _strip_reasoning(messages: list[Any]) -> list[Any]:
    """剥离 assistant 消息的 additional_kwargs 再传给 LLM。

    DeepSeek reasoner 约定：messages 里带 reasoning_content 会直接 400（禁止把上一轮的
    推理链回传）；我们的聚合推理链挂在 additional_kwargs["reasoning_content"]，必须移除。
    深拷贝避免污染 checkpoint 中的消息（最终结论仍可从 additional_kwargs 提取）。
    """
    out: list[Any] = []
    for m in messages:
        kw = getattr(m, "additional_kwargs", None)
        if kw and (kw.get("reasoning_content") or kw.get("_is_tool_round")):
            m2 = m.model_copy(deep=True)
            m2.additional_kwargs.pop("reasoning_content", None)
            m2.additional_kwargs.pop("_is_tool_round", None)
            out.append(m2)
        else:
            out.append(m)
    return out


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


def _parse_tool_calls(content: str) -> list[dict]:
    """从模型输出解析工具调用（每次仅一个）：{"tool":..,"args":..}。

    只认含 tool 键的 JSON（与最终回答天然区分）；
    返回 [{name, args, id}]，供 tools_node 执行；解析失败返回空（视为最终回答）。
    """
    candidates: list[str] = []
    for m in _JSON_BLOCK_RE.finditer(content or ""):
        candidates.append(m.group(1))
    stripped = (content or "").strip()
    if not candidates and stripped.startswith(("{", "[")):
        candidates.append(stripped)
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not data.get("tool"):
            continue
        args = data.get("args") or data.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        return [{
            "name": str(data["tool"]),
            "args": args,
            "id": f"call_{uuid.uuid4().hex[:8]}",
        }]
    return []


def build_tool_prompt(registry: ToolRegistry) -> str:
    """把注册表工具清单 + 内置工具 + 输出契约注入 system prompt（每轮重建，注册表更新即生效）。"""
    lines = [
        "你可以调用以下工具查询系统真实状态或执行已授权操作：",
        "",
    ]
    for t in registry.all():  # ToolSchema proto 对象：name/description/parameters/is_write
        lines.append(f"- {t.name}: {t.description}")
        if t.parameters:
            lines.append(f"  参数(JSON Schema): {t.parameters}")
        if t.is_write:
            lines.append("  ⚠ 写工具：仅当任务携带 suggestion_id（已审批）时才可调用；常规任务严禁调用，必须通过 suggestions 走审批闭环")
    lines += [
        "",
        "- plan_create: 为复杂多步任务建立执行计划（系统按 steps 生成待审批建议；上一步完成（含异步训练/部署完成）后下一步自动出现在审批列表）",
        '  参数(JSON Schema): {"type":"object","properties":{"summary":{"type":"string",'
        '"description":"计划摘要，如 训练并部署 LSTM 模型"},"steps":{"type":"array","items":{"type":"object",'
        '"properties":{"action_type":{"type":"string","description":"写工具名，如 training_create/serving_deploy"},'
        '"target_type":{"type":"string"},"target_id":{"type":"integer","description":"目标 ID；暂未知填 0"},'
        '"params":{"type":"object","description":"业务参数（可选）"},"reason":{"type":"string"},'
        '"priority":{"type":"string","enum":["HIGH","NORMAL","LOW"]}},'
        '"required":["action_type"]}}},"required":["summary","steps"]}',
        "",
        "- suggest_action: 提出一条写操作处置建议（系统生成待审批建议，审批通过后自动执行）。仅当需要写操作时调用",
        '  参数(JSON Schema): {"type":"object","properties":{"action_type":{"type":"string",'
        '"description":"写工具名，如 training_create/serving_deploy/training_delete/serving_undeploy/dataset_collect"},'
        '"target_type":{"type":"string"},"target_id":{"type":"integer"},'
        '"params":{"type":"object","description":"业务参数（可选）"},"reason":{"type":"string"},'
        '"priority":{"type":"string","enum":["HIGH","NORMAL","LOW"]}},'
        '"required":["action_type"]}',
        "",
        "【输出契约】",
        "1. 需要查询/执行时，只输出一个 JSON 代码块（不要包含其他内容）：",
        '   ```json {"tool": "<工具名>", "args": {...}} ```',
        "2. 工具名必须严格取自上面清单，args 必须符合对应参数 schema；会话/任务等系统参数无需填写。",
        "3. 每次只调用一个工具，等结果返回后再决定下一步。",
        "4. 当所需信息已获取、无需再调用工具时，直接输出最终回答（markdown），不要输出 JSON。",
        "5. 回答中的数据必须来自工具返回结果，严禁编造。",
    ]
    return "\n".join(lines)


async def handle_suggest_action(store: Any, ctx: TaskContext, args: dict) -> dict:
    """suggest_action 内置工具：落一条 PENDING 写操作建议（系统参数注入）。

    LLM 只填业务参数（action_type/target/params/reason/priority）；
    conversation_id/source_task_id 由本层注入。
    """
    if store is None or not store.enabled:
        log.warning("suggest_action unavailable: agent DB disabled")
        return {"status": 500, "body": "suggest_action unavailable (agent DB disabled)"}
    action = str(args.get("action_type", ""))
    if not action:
        return {"status": 400, "body": "action_type is required"}
    try:
        sid = await store.insert_suggestion({
            "source_task_id": ctx.task_id,
            "conversation_id": ctx.conversation_id,
            "action_type": action,
            "target_type": str(args.get("target_type", "")),
            "target_id": int(args.get("target_id", 0) or 0),
            "params": args.get("params") or {},
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
    """plan_create 内置工具：建 plan + 按步骤落 PENDING suggestions（系统参数注入）。

    LLM 只填业务参数（summary/steps 的 action_type/target/params/...）；
    conversation_id/source_task_id 由本层注入，避免模型填写系统字段。
    """
    if store is None or not store.enabled:
        log.warning("plan_create unavailable: agent DB disabled")
        return {"status": 500, "body": "plan_create unavailable (agent DB disabled)"}
    steps = args.get("steps") or []
    plan = {"conversation_id": ctx.conversation_id,
            "summary": str(args.get("summary", "")), "status": "RUNNING"}
    try:
        await store.upsert_plan(plan)
    except Exception as e:  # noqa: BLE001
        log.warning("plan_create upsert failed: %s", e)
        return {"status": 500, "body": f"plan create failed: {e}"}
    plan_id = plan.get("plan_id") or ""
    ids: list[str] = []
    for idx, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("action_type"):
            continue
        try:
            sid = await store.insert_suggestion({
                "plan_id": plan_id,
                "step_no": idx + 1,
                "source_task_id": ctx.task_id,
                "conversation_id": ctx.conversation_id,
                "action_type": str(s.get("action_type", "")),
                "target_type": str(s.get("target_type", "")),
                "target_id": int(s.get("target_id", 0) or 0),
                "params": s.get("params") or {},
                "reason": str(s.get("reason", "")),
                "priority": str(s.get("priority", "NORMAL")),
            })
            ids.append(sid)
        except Exception as e:  # noqa: BLE001
            log.warning("plan step suggestion failed: %s", e)
    body = json.dumps({"plan_id": plan_id, "suggestion_ids": ids, "steps": len(ids)},
                       ensure_ascii=False)
    log.info("plan created: %s steps=%d", plan_id[:8], len(ids))
    return {"status": 200, "body": body}


def build_graph(llm: Any, http: AdminHttpClient,
                registry: ToolRegistry, client: GrpcClient,
                tracker: Any = None, store: Any = None) -> Any:
    """构建并编译决策图。llm/http/registry/client/tracker/store 为进程级共享实例（闭包），ctx 走 state。
    tracker/store 不进 state（msgpack 不可序列化），写工具成功回调用 tracker 注册异步跟踪。"""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """决策节点：工具清单注入 prompt 后流式调用 LLM。

        流式策略：推理链增量实时发 thinking；正文增量先缓存，按前缀判断是否工具 JSON ——
        JSON（工具调用轮）不展示给用户，最终解析出工具调用则走 tools；否则是最终回答，
        把缓存正文作为 delta 补发（避免用户看到裸 JSON）。
        事件经 EventBatcher 聚合（40 字符 flush）降低帧数。
        """
        ctx: TaskContext = state["ctx"]
        # 关键：剥离上一轮挂的推理链（reasoner 禁止回传 reasoning_content，否则 400）
        messages = [SystemMessage(content=build_tool_prompt(registry)), *_strip_reasoning(state["messages"])]
        chunks: list[Any] = []
        reasoning_parts: list[str] = []
        pending = ""
        json_mode: Optional[bool] = None  # None=未判定 / True=工具JSON / False=正文
        batcher = EventBatcher(client, ctx.task_id)
        async for chunk in llm.astream(messages):
            chunks.append(chunk)
            rc = _chunk_reasoning(chunk)
            if rc:
                reasoning_parts.append(rc)
                await batcher.add("thinking", rc)
            text = _chunk_text(chunk)
            if not text:
                continue
            if json_mode is None:
                pending += text
                if _looks_like_json(pending):
                    json_mode = True
                elif len(pending) > 8:
                    json_mode = False
                    await batcher.add("delta", pending)
                    pending = ""
            elif json_mode:
                pending += text
            else:
                await batcher.add("delta", text)
        await batcher.flush()  # 收尾：把残余增量发完
        if not chunks:
            return {"messages": [AIMessage(content="")], "pending_tools": []}
        merged = chunks[0]
        for c in chunks[1:]:
            merged = merged + c
        # 聚合推理链全文挂到 additional_kwargs，供 TaskResult.reasoning 落库/展示
        if reasoning_parts:
            merged.additional_kwargs["reasoning_content"] = "".join(reasoning_parts)

        content = _chunk_text(merged)
        if json_mode is not False:
            tools = _parse_tool_calls(content)
            if tools:
                merged.additional_kwargs["_is_tool_round"] = True  # 结论提取时跳过工具 JSON 轮
                return {"messages": [merged], "pending_tools": tools}
            # 以 JSON 起始但解析不出工具调用（如给用户看的代码示例）→ 兜底当正文补发
            if pending:
                await client.send_event(ctx.task_id, "delta", pending)
        return {"messages": [merged], "pending_tools": []}

    async def tools_node(state: AgentState) -> dict[str, Any]:
        """工具节点：执行 pending_tools，结果以普通文本消息回填（不产生 tool 角色消息，
        保证 reasoner 等不支持 function calling 的模型多轮兼容）。
        内置工具（plan_create）走本地 handler（直写库），其余走 admin HTTP。"""
        ctx: TaskContext = state["ctx"]
        tool_msgs: list[Any] = []
        for tc in (state.get("pending_tools") or []):
            name = tc["name"]
            args = tc.get("args") or {}
            await client.send_event(ctx.task_id, "tool_call",
                                    json.dumps({"name": name, "args": args}, ensure_ascii=False))
            tool = registry.get(name)
            if name in ("plan_create", "suggest_action"):
                # 内置工具：worker 本地执行（建 plan / 落 PENDING 建议）
                if store is not None and store.enabled:
                    result = (await handle_plan_create(store, ctx, args)) if name == "plan_create" \
                        else (await handle_suggest_action(store, ctx, args))
                else:
                    result = {"status": 500, "body": f"{name} unavailable (agent DB disabled)"}
            elif tool is None:
                result = {"status": 0, "body": f"unknown tool: {name}"}
            else:
                result = await http.call(tool, args, ctx)
            # 异步写操作成功后注册跟踪（训练/部署完成后按 Plan 推进下一步）
            await _maybe_register_tracker(tracker, store, ctx, tool, result)
            body = result.get("body") if isinstance(result, dict) else result
            summary = str(body)[:500] if body is not None else ""
            await client.send_event(ctx.task_id, "tool_result",
                                    json.dumps({"name": name, "summary": summary}, ensure_ascii=False))
            tool_msgs.append(SystemMessage(
                content=f"工具 [{name}] 返回结果（仅供你分析，无需向用户复述原始 JSON，"
                        f"除非必要不要重复调用同一工具）：\n{json.dumps(result, ensure_ascii=False)}"))
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
