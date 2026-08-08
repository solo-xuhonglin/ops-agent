"""LangGraph 决策图（M3.5+，标准 LangChain 生态）。

StateGraph: START -> agent(LLM 决策) -> tools(执行工具) -> 有 tool_call 回 agent / 否则 END
消息体系为 langchain BaseMessage + add_messages reducer（checkpoint 持久化原生兼容）；
LLM 直接用 langchain_openai.ChatOpenAI（bind_tools 标准工具绑定），复用现有
ToolRegistry / AdminHttpClient / GrpcClient。

任务上下文（TaskContext）放在 state：同一图实例可被多任务（不同 thread_id）并发 ainvoke。
对外契约不变：调用方（core.handle_dispatch）仍发 TaskEvent / TaskResult。
"""
import json
import logging
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import ToolMessage
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


def build_graph(llm: ChatOpenAI, http: AdminHttpClient,
                registry: ToolRegistry, client: GrpcClient) -> Any:
    """构建并编译决策图。llm/http/registry/client 为进程级共享实例，ctx 走 state。"""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """决策节点：LLM 基于全量消息 + 动态工具 schema 产出 AIMessage。"""
        model = llm.bind_tools(registry.schemas())  # 每次绑定，注册表更新即生效
        resp = await model.ainvoke(state["messages"])
        return {"messages": [resp]}

    async def tools_node(state: AgentState) -> dict[str, Any]:
        """工具节点：执行 AIMessage 的 tool_calls，结果回填为 ToolMessage。"""
        ctx: TaskContext = state["ctx"]
        last = state["messages"][-1]
        tool_msgs: list[Any] = []
        for tc in (last.tool_calls or []):
            name = tc["name"]
            args = tc.get("args") or {}
            await client.send_event(ctx.task_id, "tool_call",
                                    f"{name}({json.dumps(args, ensure_ascii=False)})")
            tool = registry.get(name)
            if tool is None:
                result = {"status": 0, "body": f"unknown tool: {name}"}
            else:
                result = await http.call(tool, args, ctx)
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
