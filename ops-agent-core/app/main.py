"""ops-agent-core 入口：装配 LLM/工具层/任务跟踪器后启动 gRPC 重连主循环（长驻进程）。"""
import asyncio
import logging
from typing import Any

from langchain_deepseek import ChatDeepSeek
from langgraph.errors import NodeCancelledError

from app.agent import core
from app.agent.task_store import TaskStore
from app.agent.tracker import TaskTracker
from app.config import Config
from app.db import Database
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


class LLMRuntime:
    """按任务开关选择 thinking/fast LLM 实例（deepseek-v4-flash）。

    thinking 版：model_kwargs 注入 thinking enabled + reasoning_effort（推理链经
    additional_kwargs 透出，供 graph 流式发 thinking 事件）；
    fast 版：thinking disabled（无推理链，省 token，temperature 等采样参数恢复可用）。
    懒加载双实例；改配置（model/effort）时重建并替换内部引用，进程无需重启。
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._llms: dict[bool, Any] = {}

    def select(self, reasoning: bool) -> Any:
        """返回对应思考模式的 ChatDeepSeek 实例（懒加载）。"""
        llm = self._llms.get(reasoning)
        if llm is None:
            llm = self._build(reasoning)
            self._llms[reasoning] = llm
            log.info("llm runtime built: model=%s reasoning=%s", self._cfg.deepseek_model, reasoning)
        return llm

    def _build(self, reasoning: bool) -> Any:
        if reasoning:
            model_kwargs = {
                "thinking": {"type": "enabled"},
                "reasoning_effort": self._cfg.deepseek_reasoning_effort,
            }
        else:
            model_kwargs = {"thinking": {"type": "disabled"}}
        return ChatDeepSeek(
            base_url=self._cfg.deepseek_base_url,
            api_key=self._cfg.deepseek_api_key,
            model=self._cfg.deepseek_model,
            timeout=self._cfg.llm_timeout_s,
            model_kwargs=model_kwargs,
        )


async def amain() -> None:
    cfg = Config.from_env()
    client = GrpcClient(cfg, AGENTS)

    registry = ToolRegistry()
    # deepseek-v4-flash 原生 function calling：LLMRuntime 按任务开关选 thinking/fast 实例
    llm = LLMRuntime(cfg)
    http = AdminHttpClient(cfg.admin_http_base, cfg.worker_id)
    if not cfg.deepseek_api_key:
        log.warning("DEEPSEEK_API_KEY not set; tool calls will fail until configured")

    # agent 自治写库（直连 PostgreSQL，DDL 归 admin JPA；PG 变量未配则禁用持久化）
    db = Database(cfg.database_url, cfg.db_pool_min, cfg.db_pool_max,
                  host=cfg.pg_host, port=cfg.pg_port, user=cfg.pg_user,
                  password=cfg.pg_password, database=cfg.pg_database)
    await db.start()
    store = TaskStore(db, cfg.worker_id)

    # 任务跟踪器：Plan 直写库 + 异步轮询 + 决策轮（模型在观察完成后决定 plan 下一步）
    tracker = TaskTracker(store, http, client, registry, llm=llm)
    await tracker.start()

    client.on("register_ack", lambda m: _load_tools(registry, m))
    client.on("task_dispatch",
              lambda m: _run_task(client, registry, llm, http, m, cfg.max_tool_rounds,
                                  tracker, store))
    client.on("cancel_task", lambda m: _on_cancel(m))

    log.info("ops-agent-core starting: worker=%s grpc=%s llm=%s model=%s db=%s",
             cfg.worker_id, cfg.admin_grpc_addr, cfg.deepseek_base_url, cfg.deepseek_model,
             "on" if db.enabled else "off")
    try:
        await client.run()
    finally:
        await tracker.stop()
        # 不同 langchain 模型的关闭接口不一致（ChatDeepSeek 无 aclose），防御处理
        close = getattr(llm, "aclose", None)
        if close is not None:
            await close()
        await http.close()
        await db.stop()


async def _load_tools(registry: ToolRegistry, msg: agent_pb2.ServerMessage) -> None:
    registry.load(list(msg.register_ack.tools))


async def _run_task(client: GrpcClient, registry: ToolRegistry, llm: Any,
                    http: AdminHttpClient, msg: agent_pb2.ServerMessage,
                    max_rounds: int, tracker: TaskTracker, store: TaskStore) -> None:
    task_id = msg.task_dispatch.task_id
    active_tasks[task_id] = asyncio.current_task()  # 记录以便 CancelTask 精确取消
    try:
        await core.handle_dispatch(client, registry, llm, http, msg, max_rounds,
                                   tracker, store)
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
