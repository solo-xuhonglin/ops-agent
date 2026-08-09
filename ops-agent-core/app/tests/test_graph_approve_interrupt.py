"""提交审批建议后模型循环应被硬中断（human-in-the-loop 收口）。

依赖 langgraph/langchain（见 requirements.txt），在容器内运行；本地无依赖仅做 CI 校验。
全程使用 Fake 替身，不连任何真实 LLM / 数据库 / admin。
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.context import TaskContext
from app.agent.graph import build_graph, run_graph


class FakeTool:
    def __init__(self, name, is_write, parameters="{}", description=""):
        self.name = name
        self.is_write = is_write
        self.parameters = parameters
        self.description = description


class FakeRegistry:
    def __init__(self):
        self._tools = [
            FakeTool("training_create", True, "{}", "创建训练任务"),
            FakeTool("training_get", False, "{}", "查询训练任务"),
        ]

    def all(self):
        return self._tools

    def get(self, name):
        for t in self._tools:
            if t.name == name:
                return t
        return None


class FakeLLM:
    """首轮（挂工具）产出 approve_* tool_call；收口轮（无工具）只产出摘要文本。"""

    def __init__(self):
        self.bound = None
        self.calls = 0

    def bind_tools(self, tools):
        self.bound = tools
        return self

    async def astream(self, messages):
        self.calls += 1
        if not self.bound:
            yield AIMessage(content="已提交审批建议（suggestion_id=sug-0001），等待人工确认。")
        else:
            yield AIMessage(content="", tool_calls=[{
                "id": "call_1", "name": "approve_training_create", "args": {},
            }])


class FakeLLMRuntime:
    def __init__(self):
        self.llm = FakeLLM()

    def select(self, reasoning_enabled):
        return self.llm


class FakeStore:
    def __init__(self, created=True):
        self.enabled = True
        self._created = created

    async def insert_suggestion(self, d):
        return ("sug-0001", self._created)


class FakeClient:
    async def send_event(self, task_id, kind, payload):
        return None

    async def send_result(self, task_id, ok, conclusion, reasoning=""):
        return None


class FakeHttp:
    async def call(self, tool, args, ctx):
        return {"status": 200, "body": "{}"}


def _run(created):
    llm_rt = FakeLLMRuntime()
    registry = FakeRegistry()
    store = FakeStore(created=created)
    client = FakeClient()
    http = FakeHttp()
    graph = build_graph(llm_runtime=llm_rt, http=http, registry=registry,
                        client=client, store=store)
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    messages = [SystemMessage(content="sys"),
                HumanMessage(content="请发起一次训练")]
    return llm_rt, graph, ctx, messages


async def test_approve_submit_interrupts_loop_new_suggestion():
    llm_rt, graph, ctx, messages = _run(created=True)
    msgs, hit_limit = await run_graph(graph, ctx, messages, max_rounds=10)

    # 硬中断：首轮出 approve_*，收口轮出摘要后即 END，LLM 仅被调用 2 次
    assert llm_rt.llm.calls == 2, llm_rt.llm.calls
    assert hit_limit is False

    last = None
    for m in reversed(msgs):
        if getattr(m, "type", "") == "ai":
            last = m
            break
    assert last is not None
    assert not getattr(last, "tool_calls", None), "收口轮不应再产生工具调用"
    assert "审批" in (last.content or "")


async def test_approve_submit_interrupts_loop_duplicate_suggestion():
    # 命中去重（created=False，status 仍为 200）→ 同样应触发收口中断
    llm_rt, graph, ctx, messages = _run(created=False)
    msgs, hit_limit = await run_graph(graph, ctx, messages, max_rounds=10)

    assert llm_rt.llm.calls == 2, llm_rt.llm.calls
    assert hit_limit is False
    last = next(m for m in reversed(msgs) if getattr(m, "type", "") == "ai")
    assert not getattr(last, "tool_calls", None)
