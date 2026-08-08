"""LangGraph 决策图专项测试：多轮工具循环、recursion 上限恢复、并发隔离。"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.context import TaskContext
from app.agent.graph import build_graph, run_graph
from app.tools.registry import ToolRegistry
from tests.test_agent_core import FakeClient, FakeHttp, make_tool, tool_call_msg


class LoopLlm:
    """永远返回 tool_call（用于触发 recursion 上限）。"""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return tool_call_msg("training_list")


class StatelessLlm:
    """无状态 LLM：上一条是 tool 消息（刚执行完工具）→ 收敛结论；否则继续调工具。"""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if messages[-1].type == "tool":
            return AIMessage(content=f"完成（工具返回 {messages[-1].content}）")
        return tool_call_msg("training_list")


def make_ctx(task_id="t-graph"):
    return TaskContext(task_id=task_id, task_token="tok")


def initial_messages():
    return [SystemMessage(content="s"), HumanMessage(content="u")]


def make_env(responses):
    registry = ToolRegistry()
    registry.load([make_tool()])
    llm = responses if isinstance(responses, (LoopLlm, StatelessLlm)) else responses
    return registry, llm


@pytest.mark.asyncio
async def test_graph_loops_multi_round_tool_calls():
    """多轮工具调用：3 次 tool_call → 收敛，工具被调 3 次，消息含 tool 回填。"""
    client = FakeClient()
    http = FakeHttp(body='{"items": [1]}')

    class SeqLlm:
        def __init__(self):
            self.responses = [
                tool_call_msg("training_list", call_id="c1", args='{"page":0}'),
                tool_call_msg("training_list", call_id="c2", args='{"page":1}'),
                tool_call_msg("training_list", call_id="c3", args='{"page":2}'),
                AIMessage(content="查询完毕，共 3 页。"),
            ]

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return self.responses.pop(0)

    registry = ToolRegistry()
    registry.load([make_tool()])
    graph = build_graph(llm=SeqLlm(), http=http, registry=registry, client=client)

    final = await run_graph(graph, make_ctx(), initial_messages(), max_rounds=10)

    assert len(http.calls) == 3  # 工具执行 3 次
    assert [m.type for m in final].count("tool") == 3  # 3 条 tool 回填
    assert final[-1].content == "查询完毕，共 3 页。"  # 收敛结论在末尾
    # 事件流：3 个 tool_call 事件
    assert [e[1] for e in client.events].count("tool_call") == 3


@pytest.mark.asyncio
async def test_graph_recursion_limit_recovers():
    """LLM 持续调工具触发 recursion 上限：不崩溃，返回已有消息（含 tool 回填）。"""
    client = FakeClient()
    http = FakeHttp()
    registry, llm = make_env(LoopLlm())
    graph = build_graph(llm=llm, http=http, registry=registry, client=client)

    final = await run_graph(graph, make_ctx(), initial_messages(), max_rounds=3)

    # 已产生工具调用与回填，至少一条 tool 消息
    assert any(m.type == "tool" for m in final)
    assert len(http.calls) >= 1


@pytest.mark.asyncio
async def test_graph_concurrent_tasks_isolated():
    """同一图实例并发两个任务（不同 thread_id）：状态互不干扰。"""
    client = FakeClient()
    http = FakeHttp(body='{"items": [9]}')
    registry = ToolRegistry()
    registry.load([make_tool()])
    # 无状态 LLM 共享（真实场景 ChatOpenAI 亦无状态）：每个任务各跑一轮工具
    graph = build_graph(llm=StatelessLlm(), http=http, registry=registry, client=client)

    msgs_a = [SystemMessage(content="s"), HumanMessage(content="A问题")]
    msgs_b = [SystemMessage(content="s"), HumanMessage(content="B问题")]
    ta = run_graph(graph, make_ctx("task-a"), msgs_a, max_rounds=5)
    tb = run_graph(graph, make_ctx("task-b"), msgs_b, max_rounds=5)
    fa, fb = await asyncio.gather(ta, tb)

    # 各自收敛到结论，且都包含自己的工具回填
    assert fa[-1].type == "ai" and fa[-1].content.startswith("完成（")
    assert fb[-1].type == "ai" and fb[-1].content.startswith("完成（")
    # 状态未串扰：对方的消息不在自己的序列里
    assert not any(m.content == "B问题" for m in fa)
    assert not any(m.content == "A问题" for m in fb)
    assert len(http.calls) == 2  # 每个任务各执行一次工具
    assert [e[1] for e in client.events].count("tool_call") == 2
