"""MessageStore 测试：消息写入与读取（mock Database）。"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.message_store import MessageStore
from app.db import Database


class FakeDb:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[tuple[str, tuple]] = []
        self.rows: list[dict] = []
        self.row: dict | None = None

    @property
    def enabled(self):
        return self._enabled

    async def execute(self, sql, *args):
        if not self._enabled:
            return
        self.executed.append((sql, args))

    async def fetch(self, sql, *args):
        if not self._enabled:
            return []
        self.fetched.append((sql, args))
        return self.rows

    async def fetchrow(self, sql, *args):
        if not self._enabled:
            return None
        self.fetched.append((sql, args))
        return self.row


def make_store(db: FakeDb) -> MessageStore:
    return MessageStore(db)


async def test_save_round_assistant_only():
    """无工具调用：仅 1 行 ASSISTANT。"""
    db = FakeDb()
    store = make_store(db)
    await store.save_round(
        conversation_id="conv_1",
        task_id="task_1",
        round_index=0,
        assistant=AIMessage(content="Hello"),
        tool_calls=[],
        tool_results=[],
    )
    assert len(db.executed) == 1
    sql, args = db.executed[0]
    a = dict(zip(["message_id","conversation_id","kind","role","content","reasoning","status","task_id"], args))
    assert "INSERT INTO conversation_messages" in sql
    assert "ON CONFLICT (message_id)" in sql
    assert a["message_id"] == "round_task_1_0"
    assert a["conversation_id"] == "conv_1"
    assert a["kind"] == "ASSISTANT"
    assert a["role"] == "assistant"
    assert a["content"] == "Hello"


async def test_save_round_with_tools():
    """有工具调用：1 ASSISTANT + 1 TOOL_CALL + 1 TOOL_RESULT。"""
    db = FakeDb()
    store = make_store(db)
    await store.save_round(
        conversation_id="conv_1",
        task_id="task_1",
        round_index=0,
        assistant=AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "training_get", "args": {"jobId": 1}}],
        ),
        tool_calls=[{"id": "call_1", "name": "training_get", "args": {"jobId": 1}}],
        tool_results=[ToolMessage(content='{"status": "RUNNING"}', tool_call_id="call_1")],
    )
    # 3 行：ASSISTANT + TOOL_CALL + TOOL_RESULT
    assert len(db.executed) == 3
    kinds = []
    for _, args in db.executed:
        a = dict(zip(["message_id","conversation_id","kind","role","content","status"], args[:6]))
        kinds.append(a["kind"])
    assert kinds == ["ASSISTANT", "TOOL_CALL", "TOOL_RESULT"]

    # TOOL_CALL 行 - 通过 SQL 参数名定位（kwargs 顺序决定 args 位置）
    _, tc_args = db.executed[1]
    tc = dict(zip(["message_id","conversation_id","kind","role","content","status",
                   "task_id","tool_call_id","tool_name","tool_args"], tc_args))
    assert tc["message_id"] == "tc_call_1"
    assert tc["kind"] == "TOOL_CALL"
    assert tc["tool_name"] == "training_get"
    assert json.loads(tc["tool_args"]) == {"jobId": 1}

    # TOOL_RESULT 行
    _, tr_args = db.executed[2]
    tr = dict(zip(["message_id","conversation_id","kind","role","content","status",
                   "task_id","tool_call_id","tool_summary"], tr_args))
    assert tr["message_id"] == "tr_call_1"
    assert tr["kind"] == "TOOL_RESULT"
    assert tr["tool_call_id"] == "call_1"


async def test_save_round_multiple_parallel_tools():
    """并行工具调用：1 ASSISTANT + N TOOL_CALL + N TOOL_RESULT。"""
    db = FakeDb()
    store = make_store(db)
    tool_calls = [
        {"id": "c1", "name": "training_get", "args": {"jobId": 1}},
        {"id": "c2", "name": "serving_get", "args": {"endpointId": 2}},
    ]
    tool_results = [
        ToolMessage(content='{"status": "RUNNING"}', tool_call_id="c1"),
        ToolMessage(content='{"status": "SUCCEEDED"}', tool_call_id="c2"),
    ]
    await store.save_round(
        conversation_id="conv_1",
        task_id="task_1",
        round_index=0,
        assistant=AIMessage(content="", tool_calls=tool_calls),
        tool_calls=tool_calls,
        tool_results=tool_results,
    )
    assert len(db.executed) == 5  # 1 ASSISTANT + 2 TOOL_CALL + 2 TOOL_RESULT


async def test_save_round_with_reasoning():
    """ASSISTANT 消息携带 reasoning_content。"""
    db = FakeDb()
    store = make_store(db)
    assistant = AIMessage(
        content="Final answer",
        additional_kwargs={"reasoning_content": "thinking step by step"},
    )
    await store.save_round(
        conversation_id="conv_1",
        task_id="task_1",
        round_index=0,
        assistant=assistant,
        tool_calls=[],
        tool_results=[],
    )
    _, args = db.executed[0]
    a = dict(zip(["message_id","conversation_id","kind","role","content","reasoning","status","task_id"], args))
    assert a["content"] == "Final answer"
    assert a["reasoning"] == "thinking step by step"


async def test_get_messages_empty():
    """空会话返回空列表。"""
    db = FakeDb()
    store = make_store(db)
    rows = await store.get_messages("nonexistent")
    assert rows == []


async def test_get_messages_fetch_called():
    """get_messages 调用 fetch 查询。"""
    db = FakeDb()
    store = make_store(db)
    await store.get_messages("conv_1")
    assert len(db.fetched) == 1
    sql, args = db.fetched[0]
    assert "conversation_id=$1" in sql
    assert "ORDER BY id ASC" in sql
    assert args[0] == "conv_1"


async def test_delete_messages():
    """删除会话消息。"""
    db = FakeDb()
    store = make_store(db)
    await store.delete_messages("conv_1")
    assert len(db.executed) == 1
    sql, args = db.executed[0]
    assert "DELETE FROM conversation_messages" in sql
    assert args[0] == "conv_1"


async def test_disabled_db_noop():
    """数据库禁用时所有操作静默跳过。"""
    db = FakeDb(enabled=False)
    store = make_store(db)
    await store.save_round("conv_1", "task_1", 0, AIMessage(content="Hi"), [], [])
    await store.get_messages("conv_1")
    await store.delete_messages("conv_1")
    assert db.executed == []
    assert db.fetched == []


async def test_save_round_no_conversation_id():
    """无 conversation_id 时不写入。"""
    db = FakeDb()
    store = make_store(db)
    await store.save_round("", "task_1", 0, AIMessage(content="Hi"), [], [])
    assert db.executed == []


async def test_round_index_increments():
    """多轮写入的 message_id 按 round_index 区分。"""
    db = FakeDb()
    store = make_store(db)
    await store.save_round("conv_1", "task_1", 0, AIMessage(content="Round 0"), [], [])
    await store.save_round("conv_1", "task_1", 1, AIMessage(content="Round 1"), [], [])
    assert len(db.executed) == 2
    id0 = dict(zip(["message_id"], db.executed[0][1][:1]))["message_id"]
    id1 = dict(zip(["message_id"], db.executed[1][1][:1]))["message_id"]
    assert id0 == "round_task_1_0"
    assert id1 == "round_task_1_1"