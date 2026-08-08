"""任务上下文：随 TaskDispatch 传入，系统注入，任务结束即弃（LLM 不可见不可改）。"""
from dataclasses import dataclass


@dataclass
class TaskContext:
    task_id: str
    task_token: str
    target_type: str = ""
    target_id: int = 0
