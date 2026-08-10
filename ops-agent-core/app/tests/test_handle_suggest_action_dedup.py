"""handle_suggest_action 命中去重时应按现有建议状态定制 note。

回归：之前固定返回"正在等待审批或执行中，请勿重复提交。请等待审批结果"——
在并行推进（如 execute 轮与 plan_advance 轮同时追提同款审批，或建议已审批
正在派发执行时模型又提一次）会让 LLM 误判为"还在等审批"，对用户侧也掩盖
实际进度（实测案例：消息 473 显示"重复提交，已由系统去重，等待现有审批
结果落地即可"，但当时建议已 APPROVED、即将 EXECUTING，结论误导）。

要求：
- PENDING  → 提及"等待审批"
- APPROVED → 提及"已审批通过 / 派发执行"，且不允许出现"等待审批结果"
- EXECUTING → 提及"正在执行"，不允许出现"等待审批结果"
- store 不可用 / 查询失败 → 兜底 PENDING 文案
"""
from __future__ import annotations

import json

import pytest

from app.agent.context import TaskContext
from app.agent.graph import handle_suggest_action


class FakeStore:
    """模拟 TaskStore：可控 created（去重命中）+ 已存在建议的状态。

    - get_fails: 控制 get_suggestion 抛异常（不影响 insert_suggestion）
    - closed_step: 非 None 时 find_closed_step_suggestion 返回该结果（模拟同 step 已终态）
    """

    def __init__(self, enabled: bool = True, existing_status: str | None = None,
                 get_fails: bool = False, closed_step: dict | None = None):
        self._enabled = enabled
        self._existing_status = existing_status
        self._get_fails = get_fails
        self._closed_step = closed_step
        self._last_inserted: dict | None = None
        self.closed_step_calls: list[dict] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def insert_suggestion(self, s: dict) -> tuple[str, bool]:
        if not self._enabled:
            raise RuntimeError("db disabled")
        self._last_inserted = s
        # 去重命中（created=False）；保留现有 suggestion_id
        return "sug_dup_xxx", False

    async def get_suggestion(self, sid: str) -> dict | None:
        if self._get_fails:
            raise RuntimeError("get fake-fail")
        return {"suggestion_id": sid, "status": self._existing_status}

    async def find_closed_step_suggestion(self, s: dict) -> dict | None:
        self.closed_step_calls.append(s)
        # 与真实 TaskStore 行为一致：无 plan/step 或显式 retry 时不拦截
        if not s.get("plan_id") or int(s.get("step_no", 0) or 0) <= 0 or s.get("retry_of"):
            return None
        return self._closed_step


def _parse(result: dict) -> dict:
    return json.loads(result["body"])


async def test_dedup_pending_tells_wait_for_approval():
    store = FakeStore(existing_status="PENDING")
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"target_id": 7, "params": {}}, action_type="serving_deploy")
    body = _parse(result)
    assert body["duplicate"] is True
    assert body["status"] == "PENDING"
    # PENDING：必须暗示"请等待审批 / 尚未审批"
    assert ("等待审批" in body["note"]) or ("等待人工审批" in body["note"]), body["note"]
    assert "请勿重复提交" in body["note"]


async def test_dedup_approved_tells_dispatching_not_waiting():
    """APPROVED 时若仍说"等待审批"会误导模型——必须明示已审批、正在派发。"""
    store = FakeStore(existing_status="APPROVED")
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"target_id": 7, "params": {}}, action_type="serving_deploy")
    body = _parse(result)
    assert body["duplicate"] is True
    assert body["status"] == "APPROVED"
    # 必须含"已审批/审批通过/已被审批"任一语义；test 用"审"作广义断言（细粒度措辞可能调整）
    assert ("已审批" in body["note"]) or ("已被审批" in body["note"]) or ("审批通过" in body["note"]), body["note"]
    assert "等待审批结果" not in body["note"]
    # 应提示用 wait_until / 只读查询跟踪
    assert "wait_until" in body["note"]


async def test_dedup_executing_tells_already_running_not_waiting():
    """EXECUTING 时必须明确"正在执行"——避免被误判为没动。"""
    store = FakeStore(existing_status="EXECUTING")
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"target_id": 7, "params": {}}, action_type="serving_deploy")
    body = _parse(result)
    assert body["duplicate"] is True
    assert body["status"] == "EXECUTING"
    # 必须含"正在执行"语义
    assert ("正在执行" in body["note"]) or ("执行中" in body["note"]), body["note"]
    assert "等待审批结果" not in body["note"]


async def test_dedup_get_suggestion_failure_falls_back_to_pending():
    """get_suggestion 失败（DB 抖动）→ 兜底走 PENDING 文案，不抛错。"""
    store = FakeStore(existing_status=None, get_fails=True)
    # 走去重路径（insert OK），但 get_suggestion 抛错 → fallback
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"target_id": 7, "params": {}}, action_type="serving_deploy")
    body = _parse(result)
    assert body["duplicate"] is True
    # 兜底落到 PENDING 模板
    assert body["status"] == "PENDING"
    assert ("等待审批" in body["note"]) or ("等待人工审批" in body["note"])


async def test_dedup_unknown_status_falls_back_to_pending():
    """find_open_duplicate 只命中开放态；理论上不会到 EXECUTED/FAILED，
    但保险起见：未知状态走 PENDING 文案兜底。"""
    store = FakeStore(existing_status="EXECUTED")  # 关闭态不应出现，但保险
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"target_id": 7, "params": {}}, action_type="serving_deploy")
    body = _parse(result)
    assert body["duplicate"] is True
    assert body["status"] == "EXECUTED"  # 真实状态如实回传
    assert ("等待审批" in body["note"]) or ("等待人工审批" in body["note"])  # 兜底文案


async def test_dedup_returns_action_type_for_clear_text():
    """note 内含具体 action 名（serving_deploy），方便模型对号入座。"""
    store = FakeStore(existing_status="APPROVED")
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"target_id": 15, "params": {}}, action_type="serving_deploy")
    body = _parse(result)
    assert "serving_deploy" in body["note"]


# ==================== closed-step 拦截 ====================

async def test_closed_step_blocks_resubmit():
    """同 plan 同 step 已有 EXECUTED 建议 → 直接拦截，不落库，提示已完成。

    回归：实测会话 94ac02a6——wait_until 空转 103 秒后醒来的 execute 轮
    不知道 step2 训练早已 EXECUTED，又提交 training_create，被系统 REJECTED
    显示"已忽略"，用户观感混乱。修复后应在提交前就拦下并告知已完成。
    """
    store = FakeStore(closed_step={"suggestion_id": "sug_old", "status": "EXECUTED"})
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"plan_id": "p1", "step_no": 2, "datasetId": 16},
        action_type="training_create")
    body = _parse(result)
    assert body["duplicate"] is True
    assert body["suggestion_id"] == "sug_old"
    assert body["status"] == "EXECUTED"
    assert "已完成" in body["note"], body["note"]
    assert "无需重复提交" in body["note"], body["note"]
    # 没有落到 insert（未创建新卡）
    assert store._last_inserted is None


async def test_closed_step_rejected_also_blocks():
    """REJECTED 也是终态——同步骤被拒后不应再重复提同款（除非显式 retry）。"""
    store = FakeStore(closed_step={"suggestion_id": "sug_r", "status": "REJECTED"})
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"plan_id": "p1", "step_no": 1, "target_id": 9},
        action_type="dataset_create")
    body = _parse(result)
    assert body["status"] == "REJECTED"
    assert "无需重复提交" in body["note"]


async def test_closed_step_allows_explicit_retry():
    """retry_of 显式重试 → 放行（不拦截），落库走正常路径。"""
    store = FakeStore(closed_step=None)  # find_closed_step_suggestion 收到 retry_of 应返回 None
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"plan_id": "p1", "step_no": 2, "retry_of": "sug_failed",
                     "datasetId": 16}, action_type="training_create")
    # retry_of 非空 → 不查 closed-step 或查到 None → 落库（insert 走 created=False 去重路径）
    assert store.closed_step_calls == [] or store.closed_step_calls[0]["retry_of"] == "sug_failed"
    assert result["status"] == 200
    # 未命中 closed-step 拦截（duplicate 来自 insert 去重 or 新建）
    assert store._last_inserted is not None


async def test_closed_step_no_plan_skips_check():
    """无 plan/step 上下文（step_no=0）→ 不拦截，正常落库。"""
    store = FakeStore(closed_step={"suggestion_id": "sug_x", "status": "EXECUTED"})
    ctx = TaskContext(task_id="t1", task_token="tok", conversation_id="c1")
    result = await handle_suggest_action(
        store, ctx, {"target_id": 7, "params": {}}, action_type="serving_deploy")
    # 无 plan_id/step_no → find_closed_step_suggestion 内部直接返回 None
    assert result["status"] == 200
    assert store._last_inserted is not None
