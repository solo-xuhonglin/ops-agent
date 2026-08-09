"""Agent 自治写库 repo：agent_tasks / agent_plans / agent_suggestions。

单一写方约定（v3 设计）：
- worker 写业务行（task 状态/结论、plan 建改、suggestion 创建与执行结果）
- admin 只写审批动作（suggestion APPROVED/REJECTED/EXPIRED + grant_key/confirmed_*）
- DDL 归 admin JPA；本层只读写，不建表
"""
import json
import logging
import uuid
from typing import Any, Optional

from app.db import Database

log = logging.getLogger("task_store")


def _uid(prefix: str = "") -> str:
    return (prefix + "_" if prefix else "") + uuid.uuid4().hex[:12]


class TaskStore:
    def __init__(self, db: Database, worker_id: str = "") -> None:
        self.db = db
        self.worker_id = worker_id

    @property
    def enabled(self) -> bool:
        return self.db.enabled

    # ==================== agent_tasks ====================

    async def insert_task(self, task_id: str, task_type: str, conversation_id: str,
                          query: str = "", plan_id: str = "", suggestion_id: str = "") -> None:
        """worker 收到 TaskDispatch 后落 RUNNING 行（chat 轮 / execute 轮）。"""
        await self.db.execute(
            "INSERT INTO agent_tasks "
            "(task_id, task_type, plan_id, suggestion_id, conversation_id, query, "
            " status, worker_id, started_at, created_at) "
            "VALUES ($1,$2,NULLIF($3,''),NULLIF($4,''),NULLIF($5,''),$6,"
            "'RUNNING',$7,now(),now())",
            task_id, task_type, plan_id, suggestion_id, conversation_id, query, self.worker_id)
        log.info("task inserted: %s type=%s conv=%s", task_id[:8], task_type, conversation_id[:8])

    async def finish_task(self, task_id: str, status: str, conclusion: str = "",
                          reasoning: str = "") -> None:
        await self.db.execute(
            "UPDATE agent_tasks SET status=$2, conclusion=$3, reasoning=$4, "
            "finished_at=now(), updated_at=now() WHERE task_id=$1",
            task_id, status, conclusion, reasoning)

    async def cancel_task(self, task_id: str, reason: str = "") -> None:
        await self.db.execute(
            "UPDATE agent_tasks SET status='CANCELLED', conclusion=$2, "
            "finished_at=now(), updated_at=now() "
            "WHERE task_id=$1 AND status IN ('DISPATCHED','RUNNING')",
            task_id, reason)

    async def get_task(self, task_id: str) -> Optional[dict]:
        return await self.db.fetchrow("SELECT * FROM agent_tasks WHERE task_id=$1", task_id)

    async def list_stuck_running(self, cutoff: Any) -> list[dict]:
        """超时扫描：RUNNING 且 started_at 早于 cutoff（worker 自治超时）。"""
        return await self.db.fetch(
            "SELECT * FROM agent_tasks WHERE status='RUNNING' AND started_at < $1", cutoff)

    # ==================== agent_plans ====================

    async def upsert_plan(self, plan: dict) -> None:
        """conversation 级 plan upsert（agent 建立/主动修改）。plan: {plan_id, conversation_id, summary, status}"""
        conv = plan.get("conversation_id") or ""
        if not conv:
            return
        pid = plan.get("plan_id") or _uid("plan")
        plan["plan_id"] = pid
        await self.db.execute(
            "INSERT INTO agent_plans (plan_id, conversation_id, summary, status, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,now(),now()) "
            "ON CONFLICT (plan_id) DO UPDATE SET summary=$3, status=$4, updated_at=now()",
            pid, conv, plan.get("summary", ""), plan.get("status", "RUNNING"))
        log.info("plan upserted: %s conv=%s status=%s", pid[:8], conv[:8], plan.get("status"))

    async def update_plan_status(self, plan_id: str, status: str) -> None:
        await self.db.execute(
            "UPDATE agent_plans SET status=$2, updated_at=now() WHERE plan_id=$1",
            plan_id, status)

    async def get_plan(self, plan_id: str) -> Optional[dict]:
        return await self.db.fetchrow("SELECT * FROM agent_plans WHERE plan_id=$1", plan_id)

    async def list_active_plans(self) -> list[dict]:
        """恢复用：未完结的 plan（PLANNED/RUNNING）。"""
        return await self.db.fetch(
            "SELECT * FROM agent_plans WHERE status IN ('PLANNED','RUNNING') ORDER BY created_at")

    async def get_active_plan(self, conversation_id: str) -> Optional[dict]:
        """会话当前的活跃 plan（PLANNED/RUNNING，取最新一条）。"""
        return await self.db.fetchrow(
            "SELECT * FROM agent_plans WHERE conversation_id=$1 "
            "AND status IN ('PLANNED','RUNNING') ORDER BY created_at DESC LIMIT 1",
            conversation_id)

    # ==================== agent_suggestions ====================

    async def insert_suggestion(self, s: dict) -> str:
        """写一条 PENDING 建议；返回 suggestion_id。"""
        sid = s.get("suggestion_id") or _uid("sug")
        await self.db.execute(
            "INSERT INTO agent_suggestions "
            "(suggestion_id, plan_id, step_no, source_task_id, conversation_id, "
            " action_type, target_type, target_id, params, reason, priority, "
            " status, created_at, updated_at) "
            "VALUES ($1,NULLIF($2,''),$3,$4,$5,$6,$7,$8,$9,$10,$11,"
            "'PENDING',now(),now())",
            sid, s.get("plan_id", ""), s.get("step_no"), s.get("source_task_id", ""),
            s.get("conversation_id", ""), s.get("action_type", ""), s.get("target_type", ""),
            int(s.get("target_id", 0)), json.dumps(s.get("params", {}), ensure_ascii=False),
            s.get("reason", ""), s.get("priority", "NORMAL"))
        return sid

    async def mark_suggestion_executing(self, suggestion_id: str) -> None:
        """approve 后任务下发：PENDING/APPROVED → EXECUTING（条件更新防竞争）。"""
        await self.db.execute(
            "UPDATE agent_suggestions SET status='EXECUTING', updated_at=now() "
            "WHERE suggestion_id=$1 AND status IN ('PENDING','APPROVED')", suggestion_id)

    async def update_suggestion_result(self, suggestion_id: str, status: str,
                                       result: str = "") -> None:
        """execute 执行结果回写：APPROVED/EXECUTING → EXECUTED/FAILED/CANCELLED（条件更新）。"""
        await self.db.execute(
            "UPDATE agent_suggestions SET status=$2, result=$3, executed_at=now(), updated_at=now() "
            "WHERE suggestion_id=$1 AND status IN ('APPROVED','EXECUTING')",
            suggestion_id, status, result)

    async def get_suggestion(self, suggestion_id: str) -> Optional[dict]:
        return await self.db.fetchrow(
            "SELECT * FROM agent_suggestions WHERE suggestion_id=$1", suggestion_id)

    async def pending_steps(self, plan_id: str) -> list[dict]:
        """plan 的待审批步骤（PENDING，按 step_no 升序）。"""
        if not plan_id:
            return []
        return await self.db.fetch(
            "SELECT * FROM agent_suggestions WHERE plan_id=$1 AND status='PENDING' ORDER BY step_no",
            plan_id)

    async def plan_steps(self, plan_id: str) -> list[dict]:
        if not plan_id:
            return []
        return await self.db.fetch(
            "SELECT * FROM agent_suggestions WHERE plan_id=$1 ORDER BY step_no", plan_id)
