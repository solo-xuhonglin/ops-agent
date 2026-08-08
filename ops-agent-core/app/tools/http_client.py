"""工具执行层：HTTP 直调 admin 现有 REST API。

安全约定（设计第 5 节）：系统参数（taskToken / worker 标识 / taskId / grantKey）
全部由本层代码注入，LLM 只能填业务参数 —— prompt injection 碰不到鉴权链路。
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.transport import agent_pb2
from app.tools.grants import GrantStore

log = logging.getLogger("tools.http_client")


@dataclass
class TaskContext:
    """任务上下文（系统注入，非 LLM 可控）：随 TaskDispatch 传入，任务结束即弃。"""
    task_id: str
    task_token: str
    target_type: str = ""
    target_id: int = 0


class AdminHttpClient:
    def __init__(self, base_url: str, worker_id: str, grants: Optional[GrantStore] = None,
                 timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.grants = grants or GrantStore()
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def close(self) -> None:
        await self._http.aclose()

    async def call(self, tool: agent_pb2.ToolSchema, args: dict, ctx: TaskContext) -> dict[str, Any]:
        """执行一次工具调用：写工具先查 grant（无授权不执行）→ 注入系统头 → 请求 → 返回 {status, body}。"""
        if tool.is_write:
            grant_key = self.grants.lookup(tool.name, self._candidate_target_ids(tool, args))
            if grant_key is None:
                log.warning("write tool without grant, skipped: %s (await human approval)", tool.name)
                return {"status": 403,
                        "body": "write operation not granted yet (await human approval then retry)"}

        path, query, body = self._render(tool, args)
        headers = {
            "Authorization": f"Bearer {ctx.task_token}",
            "X-Agent-Worker": self.worker_id,
            "X-Agent-Task": ctx.task_id,
            "Content-Type": "application/json",
        }
        if tool.is_write:
            headers["X-Grant-Key"] = grant_key  # type: ignore[assignment]
        url = self.base_url + path
        method = tool.http_method.upper()
        log.info("tool call: %s %s (args=%s write=%s)", method, path, query or body, tool.is_write)
        try:
            if method == "GET":
                resp = await self._http.get(url, headers=headers, params=query or None)
            else:
                resp = await self._http.request(method, url, headers=headers, json=body or {})
            text = resp.text
            log.info("tool response: %s %s status=%s", method, path, resp.status_code)
        except httpx.HTTPError as e:
            return {"status": 0, "body": f"HTTP error: {e}"}
        return {"status": resp.status_code, "body": text}

    @staticmethod
    def _candidate_target_ids(tool: agent_pb2.ToolSchema, args: dict) -> list[str]:
        """候选 targetId：路径模板变量 + 数值参数（写工具授权按 action+targetId 匹配）。"""
        ids: list[str] = []
        for key, value in args.items():
            if "{" + key + "}" in tool.path_template:
                ids.append(str(value))
            elif isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
                ids.append(str(value))
        return ids

    def _render(self, tool: agent_pb2.ToolSchema, args: dict) -> tuple[str, dict, Optional[dict]]:
        """{jobId} 之类模板变量填入 path；GET 剩余参数走 query，POST/DELETE 走 body。"""
        args = dict(args)
        path = tool.path_template
        for key in list(args):
            token = "{" + key + "}"
            if token in path:
                path = path.replace(token, str(args.pop(key)))
        if tool.http_method.upper() == "GET":
            return path, args, None
        return path, {}, args or None
