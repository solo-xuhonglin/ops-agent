"""gRPC 双向流 client：出站拨号 admin → 注册 → 收消息 → 断线指数退避重连。

设计约束：agent 零监听端口，所有交互（任务下发/事件回推/结果返回/心跳）都在这条流上。
一条流承载 worker 内所有逻辑 agent（消息按 task_id 路由，本文件不关心 agent 语义）。
"""
import asyncio
import logging
from typing import Awaitable, Callable, Optional

import grpc

from app.config import Config
from app.transport import agent_pb2, agent_pb2_grpc

log = logging.getLogger("grpc_client")

MessageHandler = Callable[[agent_pb2.ServerMessage], Awaitable[None]]


class GrpcClient:
    def __init__(self, config: Config, agents: list[tuple[str, list[str]]]):
        self.cfg = config
        self.agents = agents
        self._channel: Optional[grpc.aio.Channel] = None
        self._stream: Optional[grpc.aio.StreamStreamCall] = None
        self._callbacks: dict[str, MessageHandler] = {}
        self._seq = 0

    # ---- 生命周期 ----

    async def run(self) -> None:
        """主循环：连接 → 处理；断开后按指数退避重连，永不退出（进程级长驻）。"""
        backoff = self.cfg.reconnect_min_s
        while True:
            try:
                await self.connect_once()
                log.info("connected to admin %s", self.cfg.admin_grpc_addr)
                backoff = self.cfg.reconnect_min_s
            except Exception as e:  # noqa: BLE001 - 连接失败或流中断均进入重连
                log.warning("connection lost (%s); retry in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.cfg.reconnect_max_s)

    async def connect_once(self) -> None:
        self._channel = grpc.aio.insecure_channel(self.cfg.admin_grpc_addr)
        stub = agent_pb2_grpc.AgentServiceStub(self._channel)
        self._stream = stub.Connect()
        await self._send(agent_pb2.ClientMessage(
            register=agent_pb2.Register(
                worker_id=self.cfg.worker_id,
                agents=[agent_pb2.AgentInfo(agent_id=a, capabilities=c)
                        for a, c in self.agents])))
        log.info("registered as worker=%s agents=%s", self.cfg.worker_id,
                 [a for a, _ in self.agents])
        async for msg in self._stream:
            self._route(msg)

    # ---- 发送 ----

    async def _send(self, msg: agent_pb2.ClientMessage) -> None:
        await self._stream.write(msg)

    async def send_event(self, task_id: str, event_type: str, content: str) -> None:
        self._seq += 1
        await self._send(agent_pb2.ClientMessage(
            task_event=agent_pb2.TaskEvent(
                task_id=task_id, seq=self._seq,
                event_type=event_type, content=content)))

    async def send_result(self, task_id: str, ok: bool, conclusion: str,
                          error: str = "", reasoning: str = "") -> None:
        await self._send(agent_pb2.ClientMessage(
            task_result=agent_pb2.TaskResult(
                task_id=task_id, ok=ok, conclusion=conclusion, error=error,
                reasoning=reasoning)))

    # ---- 接收路由 ----

    def on(self, kind: str, handler: MessageHandler) -> None:
        """注册 server 消息回调（kind 为 ServerMessage oneof 字段名）。"""
        self._callbacks[kind] = handler

    def _route(self, msg: agent_pb2.ServerMessage) -> None:
        kind = msg.WhichOneof("msg")
        if kind == "ping":
            asyncio.create_task(self._reply_pong(msg.ping.ts))
            return
        handler = self._callbacks.get(kind)
        if handler:
            asyncio.create_task(handler(msg))
        elif kind == "register_ack":
            log.info("admin ack: %s", msg.register_ack.message)
        else:
            log.warning("no handler for server message: %s", kind)

    async def _reply_pong(self, ts: int) -> None:
        try:
            await self._send(agent_pb2.ClientMessage(
                pong=agent_pb2.Pong(ts=ts)))
        except Exception as e:  # noqa: BLE001
            log.warning("pong send failed: %s", e)
