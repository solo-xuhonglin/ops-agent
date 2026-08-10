"""Agent 自治写库 repo：agent_tasks / agent_plans / agent_suggestions。

单一写方约定：
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
        """conversation 级 plan upsert（agent 建立/主动修改）。
        plan: {plan_id, conversation_id, summary, status, steps}，steps 为步骤清单（JSON 存储）。"""
        conv = plan.get("conversation_id") or ""
        if not conv:
            return
        pid = plan.get("plan_id") or _uid("plan")
        plan["plan_id"] = pid
        steps = json.dumps(plan.get("steps") or [], ensure_ascii=False)
        await self.db.execute(
            "INSERT INTO agent_plans (plan_id, conversation_id, summary, steps, status, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,now(),now()) "
            "ON CONFLICT (plan_id) DO UPDATE SET summary=$3, steps=$4, status=$5, updated_at=now()",
            pid, conv, plan.get("summary", ""), steps, plan.get("status", "RUNNING"))
        log.info("plan upserted: %s conv=%s status=%s", pid[:8], conv[:8], plan.get("status"))

    async def update_plan_status(self, plan_id: str, status: str) -> None:
        await self.db.execute(
            "UPDATE agent_plans SET status=$2, updated_at=now() WHERE plan_id=$1",
            plan_id, status)

    async def update_plan_step(self, plan_id: str, step_no: int, status: str,
                               note: str = "") -> None:
        """plan_update：更新 steps 清单中某一步的状态（模型掌舵步骤状态）。"""
        plan = await self.get_plan(plan_id)
        if not plan:
            return
        steps = plan.get("steps") or []  # get_plan 已解析 JSON
        for s in steps:
            if s.get("step_no") == step_no:
                s["status"] = status
                if note:
                    s["note"] = note
                break
        await self.db.execute(
            "UPDATE agent_plans SET steps=$2, updated_at=now() WHERE plan_id=$1",
            plan_id, json.dumps(steps, ensure_ascii=False))

    async def get_plan(self, plan_id: str) -> Optional[dict]:
        plan = await self.db.fetchrow("SELECT * FROM agent_plans WHERE plan_id=$1", plan_id)
        if plan and plan.get("steps"):
            plan["steps"] = json.loads(plan["steps"])
        return plan

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

    async def find_open_duplicate(self, s: dict) -> Optional[str]:
        """按自然键查"还活着"的同款建议，命中返回其 suggestion_id。

        自然键 = (conversation_id, action_type, target_type, target_id, params, retry_of)。
        - params 存 TEXT，转 ::jsonb 后比较：PG 的 jsonb 已规范化（key 有序、空白无关），
          等价于 canonical json，模型换参数顺序也逃不掉。CTE 加 MATERIALIZED 先物化候选行，
          保证 cast 只作用于同会话同动作的少量行（历史脏数据不会炸整条查询）。
        - retry_of 用 IS NOT DISTINCT FROM 让 NULL 参与比较：显式重试与普通建议互不合并，
          但两次相同的重试仍会合并。
        - plan_id/step_no 刻意不入键：模型重复提交时最容易漂移的就是它自己填的 step_no，
          纳入反而让重复逃逸；而"同目标同参数的活跃申请只该有一条"与挂在哪个 step 无关。
        - 只看开放状态；已关闭（EXECUTED/REJECTED/FAILED/EXPIRED/CANCELLED）表示上一轮已结束，
          重新提出同款 = 全新请求（典型的"上次失败换参重试"），允许新建。
        """
        conversation_id = str(s.get("conversation_id", ""))
        action_type = str(s.get("action_type", ""))
        if not conversation_id or not action_type:
            return None
        row = await self.db.fetchrow(
            "WITH open_rows AS MATERIALIZED ("
            "  SELECT suggestion_id, params, created_at FROM agent_suggestions "
            "  WHERE conversation_id=$1 AND action_type=$2 "
            "    AND target_type IS NOT DISTINCT FROM NULLIF($3,'') "
            "    AND target_id IS NOT DISTINCT FROM $4 "
            "    AND retry_of IS NOT DISTINCT FROM NULLIF($5,'') "
            "    AND status IN ('PENDING','APPROVED','EXECUTING')"
            ") "
            "SELECT suggestion_id FROM open_rows "
            "WHERE NULLIF(params,'')::jsonb IS NOT DISTINCT FROM $6::jsonb "
            "ORDER BY created_at DESC LIMIT 1",
            conversation_id, action_type, str(s.get("target_type", "")),
            int(s.get("target_id", 0) or 0), str(s.get("retry_of", "")),
            json.dumps(s.get("params", {}), ensure_ascii=False))
        return row.get("suggestion_id") if row else None

    async def find_closed_step_suggestion(self, s: dict) -> Optional[dict]:
        """查同 plan 同 step 同 action 的"已关闭"建议（EXECUTED/REJECTED/FAILED/EXPIRED/CANCELLED）。

        用途：堵住"迟到任务重复提交已完成的步骤审批"——典型场景是 wait_until 空转/超时后醒来，
        模型上下文还是旧快照，不知道该步骤早已被另一条执行链完成（实测会话 94ac02a6：
        sug_befc2d7f09bd 在 step2 早已 EXECUTED 后又提交，被系统 REJECTED 显示"已忽略"）。
        - 只按 plan_id+step_no+action_type 定位（不比较 params/target），同一步骤重复审批必拦；
        - 显式重试（retry_of 非空）放行：上次失败换参重试是正常需求；
        - 无 plan/step 上下文（step_no=0）时返回 None，不拦截无主建议。
        """
        plan_id = str(s.get("plan_id", ""))
        step_no = int(s.get("step_no", 0) or 0)
        action_type = str(s.get("action_type", ""))
        retry_of = str(s.get("retry_of", ""))
        if not plan_id or step_no <= 0 or not action_type or retry_of:
            return None
        row = await self.db.fetchrow(
            "SELECT suggestion_id, status FROM agent_suggestions "
            "WHERE conversation_id=$1 AND plan_id=$2 AND step_no=$3 AND action_type=$4 "
            "  AND status IN ('EXECUTED','REJECTED','FAILED','EXPIRED','CANCELLED') "
            "ORDER BY id ASC LIMIT 1",
            str(s.get("conversation_id", "")), plan_id, step_no, action_type)
        if not row:
            return None
        return {"suggestion_id": row["suggestion_id"], "status": row["status"]}

    async def insert_suggestion(self, s: dict) -> tuple[str, bool]:
        """写一条 PENDING 建议；返回 (suggestion_id, created)。

        幂等：先按自然键查开放态的同款建议，命中则复用其 id 且不写库（created=False）。
        这是"一次提问刷出多张审批卡"的硬兜底——即便模型无视提示连发多条相同 approve_*，
        库里也只有一行、界面上也只有一张卡。
        """
        existing = await self.find_open_duplicate(s)
        if existing:
            log.info("suggestion deduped: reuse %s action=%s",
                     existing[:8], s.get("action_type", ""))
            return existing, False
        sid = s.get("suggestion_id") or _uid("sug")
        await self.db.execute(
            "INSERT INTO agent_suggestions "
            "(suggestion_id, plan_id, step_no, source_task_id, conversation_id, "
            " action_type, target_type, target_id, params, reason, priority, "
            " status, retry_of, created_at, updated_at) "
            "VALUES ($1,NULLIF($2,''),$3,$4,$5,$6,$7,$8,$9,$10,$11,"
            "'PENDING',NULLIF($12,''),now(),now())",
            sid, s.get("plan_id", ""), s.get("step_no"), s.get("source_task_id", ""),
            s.get("conversation_id", ""), s.get("action_type", ""), s.get("target_type", ""),
            int(s.get("target_id", 0)), json.dumps(s.get("params", {}), ensure_ascii=False),
            s.get("reason", ""), s.get("priority", "NORMAL"), s.get("retry_of", ""))
        return sid, True

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
        """plan 的待审批步骤建议（PENDING，按 step_no 升序）。"""
        if not plan_id:
            return []
        return await self.db.fetch(
            "SELECT * FROM agent_suggestions WHERE plan_id=$1 AND status='PENDING' ORDER BY step_no",
            plan_id)

    async def suggestions_of_plan(self, plan_id: str) -> list[dict]:
        if not plan_id:
            return []
        return await self.db.fetch(
            "SELECT * FROM agent_suggestions WHERE plan_id=$1 ORDER BY step_no", plan_id)
