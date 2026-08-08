"""LangGraph 决策图（M3.5）。

StateGraph: START -> agent(LLM 决策) -> tools(执行工具) -> 有 tool_call 回 agent / 否则 END
节点直接复用自研组件（DeepSeekClient / ToolRegistry / AdminHttpClient / GrpcClient），
不引入 langchain 全家桶；checkpointer 用 MemorySaver（thread_id=task_id）——
后续换持久化 saver（如 Sqlite/Postgres）即可支持任务中断恢复与多步工作流。

任务上下文（TaskContext）放在 state 而非闭包捕获：同一图实例可被多个任务
（不同 thread_id）并发 ainvoke，状态互不干扰。

对外契约不变：调用方（core.handle_dispatch）仍发 TaskEvent / TaskResult。
"""
import json
import logging
from operator import add
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from app.llm.deepseek import DeepSeekClient, parse_tool_calls
from app.tools.http_client import AdminHttpClient, TaskContext
from app.tools.registry import ToolRegistry
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.graph")


class AgentState(TypedDict):
    # 消息为 OpenAI 协议 dict（DeepSeek 返回原样），用纯追加 reducer 保持 dict 结构
    # （add_messages 会转成 langchain AIMessage，破坏 parse_tool_calls 的 dict 语义）
    messages: Annotated[list[dict[str, Any]], add]
    ctx: TaskContext


def build_graph(llm: DeepSeekClient, http: AdminHttpClient,
                registry: ToolRegistry, client: GrpcClient) -> Any:
    """构建并编译决策图。llm/http/registry/client 为进程级共享实例，ctx 走 state。"""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        """决策节点：LLM 基于全量消息 + 动态工具 schema 产出 assistant 消息。"""
        resp = await llm.chat(state["messages"], tools=registry.schemas())
        return {"messages": [resp]}

    async def tools_node(state: AgentState) -> dict[str, Any]:
        """工具节点：执行 assistant 消息中的所有 tool_call，结果回填为 tool 消息。"""
        ctx: TaskContext = state["ctx"]
        last = state["messages"][-1]
        tool_msgs: list[dict[str, Any]] = []
        for call_id, name, args in parse_tool_calls(last):
            await client.send_event(ctx.task_id, "tool_call",
                                    f"{name}({json.dumps(args, ensure_ascii=False)})")
            tool = registry.get(name)
            if tool is None:
                result = {"status": 0, "body": f"unknown tool: {name}"}
            else:
                result = await http.call(tool, args, ctx)
            tool_msgs.append({"role": "tool", "tool_call_id": call_id, "name": name,
                              "content": json.dumps(result, ensure_ascii=False)})
        return {"messages": tool_msgs}

    def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if parse_tool_calls(last):
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
                    messages: list[dict[str, Any]], max_rounds: int = 10) -> list[dict[str, Any]]:
    """执行图，返回收敛后的完整消息列表。

    达到 recursion 上限（LLM 持续调工具）时抛 GraphRecursionError —— 从 checkpoint
    快照取已产生的消息，保证"轮数耗尽也能正常收敛产出结论"（与原自研循环行为一致）。
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
