"""处置授权存储：admin approve 后沿 gRPC 推 AuthorizationGrant，写工具调用时按 action+targetId 匹配注入。

grantKey 不进 LLM 上下文；只有 http_client 代码层按 action/target 精确匹配后自动携带。
"""
import logging
import time
from typing import Optional

from app.transport import agent_pb2

log = logging.getLogger("tools.grants")


class GrantStore:
    def __init__(self) -> None:
        # action -> {targetId(str): (grant_key, expires_monotonic)}
        self._grants: dict[str, dict[str, tuple[str, float]]] = {}

    def add(self, grant: agent_pb2.AuthorizationGrant) -> None:
        key = grant.grant_key
        if not key:
            return
        self._grants.setdefault(grant.action_type, {})[str(grant.target_id)] = (
            key, time.monotonic() + grant.ttl_seconds)
        log.info("grant stored: action=%s targetId=%s ttl=%ss",
                 grant.action_type, grant.target_id, grant.ttl_seconds)

    def lookup(self, action: str, candidate_target_ids: list[str]) -> Optional[str]:
        """按 action + 候选 targetId 匹配有效 grantKey（惰性清理过期项）。"""
        bucket = self._grants.get(action)
        if not bucket:
            return None
        now = time.monotonic()
        for tid in candidate_target_ids:
            entry = bucket.get(tid)
            if entry is None:
                continue
            if entry[1] > now:
                return entry[0]
            bucket.pop(tid, None)  # 过期清理
        return None
