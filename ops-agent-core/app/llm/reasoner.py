"""deepseek-reasoner 专用 LLM：直接走 OpenAI SDK 流式，透出推理链。

langchain-openai 的 ChatOpenAI 在 astream 时会丢弃 DeepSeek 扩展字段
reasoning_content（chunk 里只剩 content），导致推理链无法实时回显。
本类用 OpenAI AsyncOpenAI 直接调用，把 delta.reasoning_content 挂到
AIMessageChunk.additional_kwargs["reasoning_content"]，graph 的
_chunk_reasoning 即可读取；其余（content / tool 契约）与 ChatOpenAI 兼容。

约定：deepseek-reasoner 不支持 temperature/top_p/tools 参数，也不支持把
上一轮的 reasoning_content 回传（会 400）——本类序列化消息时只带 role/content，
天然满足。
"""
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessageChunk, BaseMessage
from openai import AsyncOpenAI

_ROLE_MAP = {
    "system": "system",
    "human": "user",
    "ai": "assistant",
    "user": "user",
    "assistant": "assistant",
}


def _to_openai_message(m: BaseMessage) -> dict[str, str]:
    """langchain 消息 → OpenAI 消息（只带 role/content，不带 reasoning_content）。"""
    content = m.content
    if not isinstance(content, str):
        content = str(content)
    return {"role": _ROLE_MAP.get(m.type, "user"), "content": content}


class DeepSeekReasonerLLM:
    """deepseek-reasoner 流式调用（支持 aclose，供进程退出时释放连接）。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.model = model
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=1,
        )

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        payload = [_to_openai_message(m) for m in messages]
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=payload,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            content = getattr(delta, "content", None)
            if not reasoning and not content:
                continue
            kwargs: dict[str, Any] = {}
            if reasoning:
                kwargs["reasoning_content"] = reasoning
            yield AIMessageChunk(
                content=content or "",
                additional_kwargs=kwargs,
            )

    async def aclose(self) -> None:
        await self._client.close()
