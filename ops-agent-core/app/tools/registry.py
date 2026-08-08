"""工具注册表：由 RegisterAck 下发的 ToolSchema 动态构建（agent 零硬编码）。"""
import json
import logging
from typing import Optional

from app.transport import agent_pb2

log = logging.getLogger("tools.registry")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, agent_pb2.ToolSchema] = {}

    def load(self, schemas: list[agent_pb2.ToolSchema]) -> None:
        self._tools = {s.name: s for s in schemas}
        log.info("tool registry loaded: %s", list(self._tools))

    def get(self, name: str) -> Optional[agent_pb2.ToolSchema]:
        return self._tools.get(name)

    def all(self) -> list[agent_pb2.ToolSchema]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        """OpenAI function calling 格式（注入 LLM tools 参数）。"""
        result = []
        for t in self._tools.values():
            try:
                parameters = json.loads(t.parameters or "{}")
            except json.JSONDecodeError:
                parameters = {"type": "object", "properties": {}}
            result.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": parameters,
                },
            })
        return result
