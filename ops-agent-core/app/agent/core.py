"""Agent 决策循环（M1 stub）：收到 TaskDispatch → 发进度事件 → 返回固定结论。

M2 起由 function-calling 循环（DeepSeek + 工具调用）替换 handle_dispatch 内部实现，
对外契约（TaskEvent → TaskResult）保持不变。
"""
import asyncio
import logging

from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.core")


async def handle_dispatch(client: GrpcClient, msg: agent_pb2.ServerMessage) -> None:
    d = msg.task_dispatch
    task_id = d.task_id
    log.info("dispatch received: task=%s type=%s target=%s/%s",
             task_id, d.task_type, d.target_type, d.target_id)
    await client.send_event(task_id, "progress", f"received task [{d.task_type}]")
    await asyncio.sleep(0.2)  # 模拟处理耗时
    await client.send_event(task_id, "progress", "M1 stub: no LLM yet")
    await client.send_result(
        task_id, ok=True,
        conclusion=f"M1 connectivity OK: task {task_id} ({d.task_type}) processed by stub")
