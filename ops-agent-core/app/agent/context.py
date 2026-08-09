"""任务上下文：随 TaskDispatch 传入，系统注入，任务结束即弃（LLM 不可见不可改）。"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.agent.tracker import TaskTracker  # noqa: F401


@dataclass
class TaskContext:
    task_id: str
    task_token: str
    target_type: str = ""
    target_id: int = 0
    conversation_id: str = ""   # 所属会话（agent 定位 Plan；非会话任务为空）
    suggestion_id: int = 0      # >0：执行已审批写操作（grantKey 已下发，可调写工具）
    tracker: Optional["TaskTracker"] = None  # 写工具响应后注册异步跟踪（main 装配）
