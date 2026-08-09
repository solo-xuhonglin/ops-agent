"""LangGraph 决策图（M3.5+，标准 LangChain 生态）。

StateGraph: START -> agent(LLM 决策) -> tools(执行工具) -> 有 tool_call 回 agent / 否则 END
消息体系为 langchain BaseMessage + add_messages reducer（checkpoint 持久化原生兼容）；
LLM 直接用 langchain_openai.ChatOpenAI（bind_tools 标准工具绑定），复用现有
ToolRegistry / AdminHttpClient / GrpcClient。

任务上下文（TaskContext）放在 state：同一图实例可被多任务（不同 thread_id）并发 ainvoke。
对外契约：调用方（core.handle_dispatch）仍收 TaskEvent / TaskResult；
对话补强后 agent 节点流式产出（astream），增量以 thinking/delta 事件实时回传，
聚合后的完整推理链挂在最终 AIMessage.additional_kwargs["reasoning_content"]。
"""
import json
import logging
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.tools.http_client import AdminHttpClient, TaskContext
from app.tools.registry import ToolRegistry
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.graph")


class AgentState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    ctx: TaskContext


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


def build_graph(llm: ChatOpenAI, http: AdminHttpClient,
                registry: ToolRegistry, client: GrpcClient) -> Any:
    """构建并编译决策图。llm/http/registry/client 为进程级共享实例，ctx 走 state。"""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """决策节点：LLM 流式产出（astream），增量发 thinking/delta 事件后聚合为完整 AIMessage。"""
        model = llm.bind_tools(registry.schemas())  # 每次绑定，注册表更新即生效
        ctx: TaskContext = state["ctx"]
        chunks: list[Any] = []
        reasoning_parts: list[str] = []
        async for chunk in model.astream(state["messages"]):
            chunks.append(chunk)
            rc = _chunk_reasoning(chunk)
            if rc:
                reasoning_parts.append(rc)
                await client.send_event(ctx.task_id, "thinking", rc)
            text = _chunk_text(chunk)
            if text:
                await client.send_event(ctx.task_id, "delta", text)
        if not chunks:
            return {"messages": [AIMessage(content="")]}
        merged = chunks[0]
        for c in chunks[1:]:
            merged = merged + c
        # 聚合推理链全文挂到 additional_kwargs，供 TaskResult.reasoning 落库/展示
        if reasoning_parts:
            merged.additional_kwargs["reasoning_content"] = "".join(reasoning_parts)
        return {"messages": [merged]}

    async def tools_node(state: AgentState) -> dict[str, Any]:
        """工具节点：执行 AIMessage 的 tool_calls，结果回填为 ToolMessage。"""
        ctx: TaskContext = state["ctx"]
        last = state["messages"][-1]
        tool_msgs: list[Any] = []
        for tc in (last.tool_calls or []):
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
            tool_msgs.append(ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=tc.get("id", ""),
                name=name,
            ))
        return {"messages": tool_msgs}

    def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
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
        result = await graph.ainvoke({"messages": messages, "ctx": ctx}, config=config)
        return result["messages"]
    except GraphRecursionError:
        log.warning("graph recursion limit reached: task=%s", ctx.task_id)
        snapshot = await graph.aget_state(config)
        return (snapshot.values or {}).get("messages") or messages
