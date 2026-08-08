"""Agent 决策循环（M2）：function-calling。

收到 TaskDispatch → 组装 messages → 循环调 LLM(tools=schema)：
  LLM 返回 tool_call → 查 registry → 执行（HTTP 直调 admin，系统参数由 http_client 注入）→ 回填 → 再问
  无 tool_call → 收敛 → TaskResult(conclusion)
对外契约（TaskEvent → TaskResult）与 M1 一致。
"""
import json
import logging

from app.agent.context import TaskContext
from app.llm.deepseek import DeepSeekClient, parse_tool_calls
from app.tools.http_client import AdminHttpClient
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.core")

SYSTEM_PROMPT = (
    "你是 ops-agent 的运维助手，负责诊断训练任务、推理服务(serving)、数据集与模型状态，"
    "并回答运维相关的自然语言问询。你可以调用工具查询系统真实状态；"
    "基于工具返回的数据给出简洁、准确的中文结论；信息不足时可多次调用不同工具。"
)


async def handle_dispatch(client: GrpcClient, registry: ToolRegistry,
                          llm: DeepSeekClient, http: AdminHttpClient,
                          msg: agent_pb2.ServerMessage, max_rounds: int = 10) -> None:
    d = msg.task_dispatch
    ctx = TaskContext(task_id=d.task_id, task_token=d.task_token,
                      target_type=d.target_type, target_id=d.target_id)
    await client.send_event(ctx.task_id, "progress", f"received task [{d.task_type}]")

    user_prompt = d.query or (f"请诊断目标状态：{d.target_type}={d.target_id}"
                              if d.target_id else "请汇总当前系统状态")
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        rounds = 0
        while rounds < max_rounds:
            resp = await llm.chat(messages, tools=registry.schemas())
            messages.append(resp)  # assistant message（原样回传，含 tool_calls 结构）
            tool_calls = parse_tool_calls(resp)
            if not tool_calls:
                break
            rounds += 1
            for call_id, name, args in tool_calls:
                await client.send_event(ctx.task_id, "tool_call",
                                        f"{name}({json.dumps(args, ensure_ascii=False)})")
                tool = registry.get(name)
                if tool is None:
                    result = {"status": 0, "body": f"unknown tool: {name}"}
                else:
                    result = await http.call(tool, args, ctx)
                messages.append({"role": "tool", "tool_call_id": call_id, "name": name,
                                 "content": json.dumps(result, ensure_ascii=False)})

        conclusion = _extract_conclusion(messages)
        await client.send_result(ctx.task_id, ok=True, conclusion=conclusion)
        log.info("task done: %s rounds=%s", ctx.task_id, rounds)
    except Exception as e:  # noqa: BLE001 - 单任务失败不拖垮 worker
        log.error("task failed: %s", e, exc_info=True)
        await client.send_result(ctx.task_id, ok=False,
                                 conclusion=f"task failed: {e}", error=str(e))


def _extract_conclusion(messages: list[dict]) -> str:
    """取最后一条 assistant content（有内容才用），否则提示未收敛。"""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
    return "no conclusion produced (max tool rounds reached)"
