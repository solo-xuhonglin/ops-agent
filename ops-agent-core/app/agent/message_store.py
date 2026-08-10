"""Agent 对话消息持久化（增量插入 agent_conversation_messages 表）。

每轮 LLM 循环完成后批量写入，不做流式逐 token 落库。
Agent 写 ASSISTANT/TOOL_CALL/TOOL_RESULT 行，
Admin 写 USER/APPROVAL 行（JPA）。
"""
import json
import logging
from typing import Any, Optional

from langchain_core.messages import AIMessage, ToolMessage

from app.db import Database

log = logging.getLogger("message_store")

# 消息 ID 前缀
_ID_ROUND = "round_"
_ID_TC = "tc_"
_ID_TR = "tr_"


class MessageStore:
    """对话消息持久化（增量插入，每轮循环后写入）。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    @property
    def enabled(self) -> bool:
        return self.db.enabled

    async def save_round(self, conversation_id: str, task_id: str,
                         round_index: int,
                         assistant: AIMessage,
                         tool_calls: list[dict],
                         tool_results: list[ToolMessage]) -> None:
        """保存一轮 LLM 循环产生的消息到 conversation_messages 表。

        - assistant: 本轮 LLM 产出的 AIMessage（含 reasoning_content）
        - tool_calls: [{"id","name","args"}] (可能有多个并行调用)
        - tool_results: [ToolMessage] (每个 tool_call 对应一个)
        - round_index: 当前轮次序号（从 0 开始）
        - USER 消息由 Admin 在 send() 时通过 JPA 写入，Agent 不写 USER
        """
        if not self.enabled:
            return
        if not conversation_id:
            return

        # 1. ASSISTANT 行
        assistant_id = f"{_ID_ROUND}{task_id}_{round_index}"
        content = assistant.content or ""
        reasoning = ""
        if assistant.additional_kwargs:
            reasoning = assistant.additional_kwargs.get("reasoning_content") or ""
        kwargs = {
            "message_id": assistant_id,
            "conversation_id": conversation_id,
            "kind": "ASSISTANT",
            "role": "assistant",
            "content": content,
            "reasoning": reasoning,
            "status": "completed",
            "task_id": task_id,
        }
        await self._upsert(kwargs)

        # 2. TOOL_CALL 行（每个 tool_call 一行）
        for tc in tool_calls:
            call_id = tc.get("id", "")
            tc_id = f"{_ID_TC}{call_id}"
            tc_args = json.dumps(tc.get("args", {}), ensure_ascii=False)
            tc_kwargs = {
                "message_id": tc_id,
                "conversation_id": conversation_id,
                "kind": "TOOL_CALL",
                "role": "tool",
                "content": f"调用工具 {tc.get('name', '')}",
                "status": "completed",
                "task_id": task_id,
                "tool_call_id": call_id,
                "tool_name": tc.get("name", ""),
                "tool_args": tc_args,
            }
            await self._upsert(tc_kwargs)

        # 3. TOOL_RESULT 行（每个 tool_result 一行）
        for tr in tool_results:
            tr_call_id = getattr(tr, "tool_call_id", "") or ""
            tr_id = f"{_ID_TR}{tr_call_id}"
            tr_content = str(tr.content or "")[:500]
            tr_kwargs = {
                "message_id": tr_id,
                "conversation_id": conversation_id,
                "kind": "TOOL_RESULT",
                "role": "tool",
                "content": tr_content,
                "status": "completed",
                "task_id": task_id,
                "tool_call_id": tr_call_id,
                "tool_summary": tr_content,
            }
            await self._upsert(tr_kwargs)

    async def get_messages(self, conversation_id: str) -> list[dict]:
        """按 id 升序返回会话全部消息行。"""
        if not self.enabled or not conversation_id:
            return []
        try:
            return await self.db.fetch(
                "SELECT * FROM agent_conversation_messages "
                "WHERE conversation_id=$1 ORDER BY id ASC",
                conversation_id)
        except Exception as e:
            log.warning("get_messages failed: %s", e)
            return []

    async def delete_messages(self, conversation_id: str) -> None:
        """删除会话消息（删除会话时联动）。"""
        if not self.enabled or not conversation_id:
            return
        try:
            await self.db.execute(
                "DELETE FROM agent_conversation_messages WHERE conversation_id=$1",
                conversation_id)
        except Exception as e:
            log.warning("delete_messages failed: %s", e)

    async def _upsert(self, kwargs: dict) -> None:
        """按 message_id 幂等插入/更新一行。"""
        if not self.enabled:
            return
        columns = ", ".join(kwargs.keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(kwargs)))
        values = list(kwargs.values())
        # 动态构建 UPDATE 部分
        updates = ", ".join(f"{k}=${i+1}" for i, k in enumerate(kwargs.keys()))
        sql = (
            f"INSERT INTO agent_conversation_messages ({columns}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (message_id) DO UPDATE SET {updates}"
        )
        try:
            await self.db.execute(sql, *values)
        except Exception as e:
            log.warning("message upsert failed: %s msg=%s", e, kwargs.get("message_id", "")[:20])