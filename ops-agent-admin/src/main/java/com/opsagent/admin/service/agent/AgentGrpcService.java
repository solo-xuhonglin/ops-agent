package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.*;
import io.grpc.stub.StreamObserver;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import net.devh.boot.grpc.server.service.GrpcService;
import org.springframework.scheduling.annotation.Scheduled;

import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;

/**
 * agent 双向流入口：admin 为 gRPC server（内网），agent 出站拨号后在本流上完成
 * 注册 / 事件回推 / 结果返回 / 心跳响应。
 * 约定：连接建立后首条消息必须是 Register，否则忽略后续消息。
 */
@GrpcService
@RequiredArgsConstructor
@Slf4j
public class AgentGrpcService extends AgentServiceGrpc.AgentServiceImplBase {

    private final WorkerRegistry registry;
    private final AgentTaskService taskService;

    @Override
    public StreamObserver<ClientMessage> connect(StreamObserver<ServerMessage> responseObserver) {
        return new StreamObserver<>() {
            private final AtomicReference<String> workerIdRef = new AtomicReference<>();

            @Override
            public void onNext(ClientMessage message) {
                try {
                    switch (message.getMsgCase()) {
                        case REGISTER -> handleRegister(message.getRegister(), responseObserver);
                        case TASK_EVENT -> handleEvent(message.getTaskEvent());
                        case TASK_RESULT -> handleResult(message.getTaskResult());
                        case AGENT_UPDATE -> handleAgentUpdate(message.getAgentUpdate());
                        case PONG -> touch();
                        default -> log.warn("unexpected client message: {}", message.getMsgCase());
                    }
                } catch (Exception e) {
                    log.error("handle client message failed", e);
                }
            }

            @Override
            public void onError(Throwable t) {
                log.warn("agent stream error: workerId={}, err={}", workerIdRef.get(), t.getMessage());
                unregister();
            }

            @Override
            public void onCompleted() {
                log.info("agent stream completed: workerId={}", workerIdRef.get());
                unregister();
            }

            private void handleRegister(Register register, StreamObserver<ServerMessage> observer) {
                workerIdRef.set(register.getWorkerId());
                List<AgentInfo> agents = register.getAgentsList();
                registry.register(register.getWorkerId(), agents, observer);
                observer.onNext(ServerMessage.newBuilder()
                        .setRegisterAck(RegisterAck.newBuilder()
                                .setOk(true)
                                .setMessage("registered, agents=" + agents.stream()
                                        .map(AgentInfo::getAgentId).collect(Collectors.joining(","))))
                        .build());
            }

            private void handleEvent(TaskEvent event) {
                touch();
                taskService.recordEvent(event.getTaskId(), event.getSeq(), event.getEventType(), event.getContent());
            }

            private void handleResult(TaskResult result) {
                touch();
                taskService.complete(result.getTaskId(), result.getOk(), result.getConclusion(),
                        result.getSuggestionsList(), result.getError());
            }

            private void handleAgentUpdate(AgentUpdate update) {
                touch();
                String workerId = workerIdRef.get();
                registry.get(workerId).ifPresent(entry -> entry.setAgents(update.getAgentsList()));
            }

            private void touch() {
                String workerId = workerIdRef.get();
                if (workerId != null) {
                    registry.touch(workerId);
                }
            }

            private void unregister() {
                String workerId = workerIdRef.get();
                if (workerId != null) {
                    registry.unregister(workerId);
                }
            }
        };
    }

    /** 心跳：每 30s 向在线 worker 发 Ping（Pong 回来会 touch 续期） */
    @Scheduled(fixedDelay = 30_000)
    public void heartbeatPing() {
        long ts = System.currentTimeMillis();
        registry.all().forEach(entry -> {
            try {
                entry.getResponseObserver().onNext(ServerMessage.newBuilder()
                        .setPing(Ping.newBuilder().setTs(ts))
                        .build());
            } catch (Exception e) {
                log.warn("ping failed, unregistering worker: {}", entry.getWorkerId(), e);
                registry.unregister(entry.getWorkerId());
            }
        });
    }
}
