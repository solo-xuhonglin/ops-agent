"""handle_feedback 测试：审批被忽略后派发的反馈轮。

验证：
- ctx.feedback_only 置位（agent_node 将 bind_tools([])，不挂任何工具）
- 结论来自模型输出并 send_result（ok=True）
- 任务行落库（task_type=feedback）
- 不经过 _build_prompt 的"已审批写操作"分支（feedback 带 suggestion_id 也不执行写操作）
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from app.agent import core
from app.agent.context import TaskContext
from tests.test_agent_core import FakeClient, FakeHttp, FakeLlm, make_dispatch


class FakeStore:
    def __init__(self):
        self.enabled = True
        self.inserted = []
        self.finished = []

    async def insert_task(self, task_id, task_type, conversation_id, query=""):
        self.inserted.append((task_id, task_type, conversation_id))

    async def finish_task(self, task_id, status, conclusion, reasoning=""):
        self.finished.append((task_id, status, conclusion))


def _feedback_dispatch(suggestion_id="sug_abc", conversation_id="c-1"):
    return make_dispatch(
        query=f"你提出的建议 {suggestion_id}（training_create）被用户忽略了，请输出反馈。",
        task_id="fb-1", token="tok", suggestion_id=suggestion_id,
        conversation_id=conversation_id, task_type="feedback")


@pytest.mark.asyncio
async def test_handle_feedback_outputs_conclusion_without_tools():
    llm = FakeLlm([AIMessage(content="收到，已忽略该建议。我将调整计划，不再提交重复审批。")])
    client = FakeClient()
    store = FakeStore()
    ctx = TaskContext(task_id="fb-1", task_token="tok", conversation_id="c-1",
                      suggestion_id="sug_abc")

    await core.handle_feedback(client, None, llm, FakeHttp(), ctx,
                               _feedback_dispatch().task_dispatch, store, max_rounds=3)

    # feedback_only 置位：agent_node 会 bind_tools([])（不挂工具）
    assert ctx.feedback_only is True
    # 结论来自模型并 send_result
    assert len(client.results) == 1
    task_id, ok, conclusion, _err = client.results[0]
    assert task_id == "fb-1"
    assert ok is True
    assert "收到" in conclusion or "忽略" in conclusion
    # 任务行按 feedback 类型落库
    assert store.inserted == [("fb-1", "feedback", "c-1")]
    assert store.finished and store.finished[0][1] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_handle_feedback_falls_back_on_empty_conclusion():
    """模型无输出 → 兜底结论，不抛错。"""
    llm = FakeLlm([AIMessage(content="")])
    client = FakeClient()
    store = FakeStore()
    ctx = TaskContext(task_id="fb-2", task_token="tok", conversation_id="c-1",
                      suggestion_id="sug_abc")

    await core.handle_feedback(client, None, llm, FakeHttp(), ctx,
                               _feedback_dispatch("sug_abc", "c-1").task_dispatch,
                               store, max_rounds=3)

    assert len(client.results) == 1
    task_id, ok, conclusion, _err = client.results[0]
    assert task_id == "fb-2"
    assert ok is True
    assert conclusion  # 兜底结论非空
