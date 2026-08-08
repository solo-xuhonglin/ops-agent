"""ops-agent-core 入口：装配 LLM/工具层后启动 gRPC 重连主循环（长驻进程）。"""
import asyncio
import logging

from langchain_openai import ChatOpenAI

from app.agent import core
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

# 本 worker 承载的逻辑 agent（本期单个：覆盖全部任务类型）
AGENTS = [
    ("ops-core", ["diagnose_training", "diagnose_serving",
                  "diagnose_dataset", "model_review", "question"]),
]


async def amain() -> None:
    cfg = Config.from_env()
    client = GrpcClient(cfg, AGENTS)

    registry = ToolRegistry()
    grants = GrantStore()
    llm = ChatOpenAI(
        base_url=cfg.deepseek_base_url,
        api_key=cfg.deepseek_api_key,
        model=cfg.deepseek_model,
        timeout=cfg.llm_timeout_s,
        temperature=0.3,
    )
    http = AdminHttpClient(cfg.admin_http_base, cfg.worker_id, grants)
    if not cfg.deepseek_api_key:
        log.warning("DEEPSEEK_API_KEY not set; tool calls will fail until configured")

    client.on("register_ack", lambda m: _load_tools(registry, m))
    client.on("authorization_grant", lambda m: _on_grant(grants, m))
    client.on("task_dispatch", lambda m: _run_task(client, registry, llm, http, m, cfg.max_tool_rounds))

    log.info("ops-agent-core starting: worker=%s grpc=%s llm=%s model=%s",
             cfg.worker_id, cfg.admin_grpc_addr, cfg.deepseek_base_url, cfg.deepseek_model)
    try:
        await client.run()
    finally:
        await llm.aclose()
        await http.close()


async def _load_tools(registry: ToolRegistry, msg: agent_pb2.ServerMessage) -> None:
    registry.load(list(msg.register_ack.tools))


async def _on_grant(grants: GrantStore, msg: agent_pb2.ServerMessage) -> None:
    grants.add(msg.authorization_grant)


async def _run_task(client: GrpcClient, registry: ToolRegistry, llm: ChatOpenAI,
                    http: AdminHttpClient, msg: agent_pb2.ServerMessage, max_rounds: int) -> None:
    await core.handle_dispatch(client, registry, llm, http, msg, max_rounds)


if __name__ == "__main__":
    asyncio.run(amain())
