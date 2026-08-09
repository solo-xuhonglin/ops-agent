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

from app.tools.http_client import AdminHttpClient, TaskContext
from app.tools.registry import ToolRegistry
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.graph")

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


def _parse_tool_calls(content: str) -> list[dict]:
    """从模型输出解析工具调用：单工具 {"tool":..,"args":..} 或并行 {"tools":[...]}。

    只认含 tool/tools 键的 JSON（与最终回答里的 suggestions 块天然区分）；
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
        if not isinstance(data, dict) or ("tool" not in data and "tools" not in data):
            continue
        items = data.get("tools") if isinstance(data.get("tools"), list) else [data]
        out: list[dict] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("tool"):
                continue
            args = item.get("args") or item.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            out.append({
                "name": str(item["tool"]),
                "args": args,
                "id": f"call_{uuid.uuid4().hex[:8]}",
            })
        if out:
            return out
    return []


def build_tool_prompt(registry: ToolRegistry) -> str:
    """把注册表工具清单 + 输出契约注入 system prompt（每轮重建，注册表更新即生效）。"""
    lines = [
        "你可以调用以下工具查询系统真实状态或执行已授权操作：",
        "",
    ]
    for t in registry.all():  # ToolSchema proto 对象：name/description/parameters/is_write
        lines.append(f"- {t.name}: {t.description}")
        if t.parameters:
            lines.append(f"  参数(JSON Schema): {t.parameters}")
        if t.is_write:
            lines.append("  ⚠ 写工具：仅当任务类型为 execute_suggestion（已审批）时才可调用；常规任务严禁调用，必须通过 suggestions JSON 走审批闭环")
    lines += [
        "",
        "【输出契约】",
        "1. 需要查询/执行时，只输出一个 JSON 代码块（不要包含其他内容）：",
        '   ```json {"tool": "<工具名>", "args": {...}} ```',
        "   需要并行调用多个工具时：",
        '   ```json {"tools": [{"tool": "a", "args": {...}}, {"tool": "b", "args": {...}}]} ```',
        "2. 工具名必须严格取自上面清单，args 必须符合对应参数 schema。",
        "3. 当所需信息已获取、无需再调用工具时，直接输出最终回答（markdown），不要输出 JSON。",
        "4. 回答中的数据必须来自工具返回结果，严禁编造。",
    ]
    return "\n".join(lines)


def build_graph(llm: Any, http: AdminHttpClient,
                registry: ToolRegistry, client: GrpcClient) -> Any:
    """构建并编译决策图。llm/http/registry/client 为进程级共享实例，ctx 走 state。
    llm 需实现 astream(messages)（流式产出 AIMessageChunk）；默认 ChatDeepSeek
    （langchain-deepseek，reasoning_content 挂 additional_kwargs 供 _chunk_reasoning 读取）。"""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """决策节点：工具清单注入 prompt 后流式调用 LLM。

        流式策略：推理链增量实时发 thinking；正文增量先缓存，按前缀判断是否工具 JSON ——
        JSON（工具调用轮）不展示给用户，最终解析出工具调用则走 tools；否则是最终回答，
        把缓存正文作为 delta 补发（避免用户看到裸 JSON）。
        """
        ctx: TaskContext = state["ctx"]
        # 关键：剥离上一轮挂的推理链（reasoner 禁止回传 reasoning_content，否则 400）
        messages = [SystemMessage(content=build_tool_prompt(registry)), *_strip_reasoning(state["messages"])]
        chunks: list[Any] = []
        reasoning_parts: list[str] = []
        pending = ""
        json_mode: Optional[bool] = None  # None=未判定 / True=工具JSON / False=正文
        async for chunk in llm.astream(messages):
            chunks.append(chunk)
            rc = _chunk_reasoning(chunk)
            if rc:
                reasoning_parts.append(rc)
                await client.send_event(ctx.task_id, "thinking", rc)
            text = _chunk_text(chunk)
            if not text:
                continue
            if json_mode is None:
                pending += text
                if _looks_like_json(pending):
                    json_mode = True
                elif len(pending) > 8:
                    json_mode = False
                    await client.send_event(ctx.task_id, "delta", pending)
                    pending = ""
            elif json_mode:
                pending += text
            else:
                await client.send_event(ctx.task_id, "delta", text)
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
        保证 reasoner 等不支持 function calling 的模型多轮兼容）。"""
        ctx: TaskContext = state["ctx"]
        tool_msgs: list[Any] = []
        for tc in (state.get("pending_tools") or []):
            name = tc["name"]
            args = tc.get("args") or {}
            await client.send_event(ctx.task_id, "tool_call",
                                    json.dumps({"name": name, "args": args}, ensure_ascii=False))
            tool = registry.get(name)
            if tool is None:
                result = {"status": 0, "body": f"unknown tool: {name}"}
            else:
                result = await http.call(tool, args, ctx)
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
