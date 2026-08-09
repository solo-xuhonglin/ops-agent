"""PostgreSQL 直连层（asyncpg 连接池）：agent 自治写库用。

约定：表结构 DDL 归 admin JPA（ddl-auto），worker 只读写不建表；
DATABASE_URL 未配置时降级为"禁用持久化"（工具/决策功能不受影响）。
"""
import logging
from typing import Any, Optional

import asyncpg

log = logging.getLogger("db")


class Database:
    """asyncpg 连接池封装：execute/fetch/fetchrow 三个帮助方法。"""

    def __init__(self, url: str, min_size: int = 1, max_size: int = 5) -> None:
        self.url = url
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None

    @property
    def enabled(self) -> bool:
        return bool(self.url) and self._pool is not None

    async def start(self) -> None:
        if not self.url:
            log.warning("DATABASE_URL not set; agent-side persistence disabled")
            return
        self._pool = await asyncpg.create_pool(
            self.url, min_size=self.min_size, max_size=self.max_size)
        log.info("database pool started: %s", self.url.split("@")[-1])

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, sql: str, *args: Any) -> None:
        if not self.enabled:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        if not self.enabled:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]

    async def fetchrow(self, sql: str, *args: Any) -> Optional[dict]:
        if not self.enabled:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
            return dict(row) if row else None
