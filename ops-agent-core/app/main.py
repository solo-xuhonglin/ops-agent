"""ops-agent-core 入口：装配 LLM/工具层/任务跟踪器后启动 gRPC 重连主循环（长驻进程）。"""
import asyncio
import logging
from typing import Any

from langchain_deepseek import ChatDeepSeek
from langgraph.errors import NodeCancelledError

from app.agent import core
from app.agent.tracker import TaskTracker
from app.config import Config
from app.tools.grants import GrantStore
from app.tools.http_client import AdminHttpClient
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2
from app.transport.grpc_client import GrpcClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

# 本 worker 承载的逻辑 agent（本期单个：能力面与业务诊断/问询/审批执行/Plan 推进）
AGENTS = [
    ("ops-core", ["diagnose_training", "diagnose_serving",
                  "diagnose_dataset", "model_review", "question"]),
]

# 执行中的任务：task_id -> asyncio.Task（供 CancelTask 精确取消）
active_tasks: dict[str, asyncio.Task] = {}


async def amain() -> None:
    cfg = Config.from_env()
    client = GrpcClient(cfg, AGENTS)

    registry = ToolRegistry()
    grants = GrantStore()
    # deepseek-reasoner 专用（langchain-deepseek 官方封装）：流式透出推理链
    # reasoning_content（ChatOpenAI 不提取第三方扩展字段），不支持 temperature/tools 参数
    llm = ChatDeepSeek(
        base_url=cfg.deepseek_base_url,
        api_key=cfg.deepseek_api_key,
        model=cfg.deepseek_model,
        timeout=cfg.llm_timeout_s,
    )
    http = AdminHttpClient(cfg.admin_http_base, cfg.worker_id, grants)
    if not cfg.deepseek_api_key:
        log.warning("DEEPSEEK_API_KEY not set; tool calls will fail until configured")

    # 任务跟踪器：Plan 持久化 + 异步轮询 + 自主推进（流程控制在 agent 侧）
    tracker = TaskTracker(http, client, registry)
    await tracker.start()

    client.on("register_ack", lambda m: _load_tools(registry, m))
    client.on("authorization_grant", lambda m: _on_grant(grants, m))
    client.on("task_dispatch",
              lambda m: _run_task(client, registry, llm, http, m, cfg.max_tool_rounds, tracker))
    client.on("cancel_task", lambda m: _on_cancel(m))

    log.info("ops-agent-core starting: worker=%s grpc=%s llm=%s model=%s",
             cfg.worker_id, cfg.admin_grpc_addr, cfg.deepseek_base_url, cfg.deepseek_model)
    try:
        await client.run()
    finally:
        await tracker.stop()
        # 不同 langchain 模型的关闭接口不一致（ChatDeepSeek 无 aclose），防御处理
        close = getattr(llm, "aclose", None)
        if close is not None:
            await close()
        await http.close()


async def _load_tools(registry: ToolRegistry, msg: agent_pb2.ServerMessage) -> None:
    registry.load(list(msg.register_ack.tools))


async def _on_grant(grants: GrantStore, msg: agent_pb2.ServerMessage) -> None:
    grants.add(msg.authorization_grant)


async def _run_task(client: GrpcClient, registry: ToolRegistry, llm: Any,
                    http: AdminHttpClient, msg: agent_pb2.ServerMessage,
                    max_rounds: int, tracker: TaskTracker) -> None:
    task_id = msg.task_dispatch.task_id
    active_tasks[task_id] = asyncio.current_task()  # 记录以便 CancelTask 精确取消
    try:
        await core.handle_dispatch(client, registry, llm, http, msg, max_rounds, tracker)
    except NodeCancelledError:
        # admin 取消：core 已打日志，吞掉避免 asyncio "Task exception was never retrieved" 告警
        pass
    finally:
        active_tasks.pop(task_id, None)


async def _on_cancel(msg: agent_pb2.ServerMessage) -> None:
    task_id = msg.cancel_task.task_id
    task = active_tasks.get(task_id)
    if task and not task.done():
        log.info("cancelling task %s (reason=%s)", task_id, msg.cancel_task.reason)
        task.cancel()
    else:
        log.info("cancel for unknown/inactive task %s (reason=%s)", task_id, msg.cancel_task.reason)


if __name__ == "__main__":
    asyncio.run(amain())
