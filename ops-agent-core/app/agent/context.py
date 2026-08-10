"""任务上下文：随 TaskDispatch 传入，系统注入，任务结束即弃（LLM 不可见不可改）。
注意：本对象会进 langgraph checkpoint（msgpack 序列化），只放可序列化标量。"""
from dataclasses import dataclass, field


@dataclass
class TaskContext:
    task_id: str
    task_token: str
    target_type: str = ""
    target_id: int = 0
    conversation_id: str = ""   # 所属会话（agent 定位 Plan；非会话任务为空）
    suggestion_id: str = ""     # 非空：执行已审批写操作（grant_key 随 TaskDispatch 下发）
    grant_key: str = ""         # approve 后签发（写工具调用时注入，不进 LLM 上下文）
    reasoning_enabled: bool = True  # chat：前端「深度思考」开关（true=thinking 模式）
    # 延迟注册的 Monitor 清单（写工具成功后暂存，任务收敛时按对象终态决定是否兜底注册）
    pending_trackers: list = field(default_factory=list)
