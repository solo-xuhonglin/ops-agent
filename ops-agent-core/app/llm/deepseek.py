"""DeepSeek 接入：OpenAI 兼容 /chat/completions + function calling（httpx 轻量实现）。"""
import json
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger("llm.deepseek")


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", timeout_s: float = 60.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def close(self) -> None:
        await self._http.aclose()

    async def chat(self, messages: list[dict], tools: Optional[list[dict]] = None) -> dict[str, Any]:
        """调用 chat/completions，返回 message 对象（含 content / tool_calls）。"""
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        resp = await self._http.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]


def parse_tool_calls(message: dict[str, Any]) -> list[tuple[str, str, dict]]:
    """从 LLM message 提取 (tool_call_id, tool_name, arguments dict) 列表。"""
    out: list[tuple[str, str, dict]] = []
    for tc in message.get("tool_calls") or []:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        out.append((tc.get("id", ""), name, args))
    return out
