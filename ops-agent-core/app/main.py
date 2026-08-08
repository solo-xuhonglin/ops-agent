"""ops-agent-core 入口：装配回调后启动 gRPC 重连主循环（长驻进程）。"""
import asyncio
import logging

from app.agent import core
from app.config import Config
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
    client.on("task_dispatch",
              lambda m: asyncio.create_task(core.handle_dispatch(client, m)))
    log.info("ops-agent-core starting: worker=%s grpc=%s", cfg.worker_id, cfg.admin_grpc_addr)
    await client.run()


if __name__ == "__main__":
    asyncio.run(amain())
