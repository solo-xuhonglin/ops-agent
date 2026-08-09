package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.grpc.stub.StreamObserver;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import net.devh.boot.grpc.server.service.GrpcService;
import org.springframework.scheduling.annotation.Scheduled;

import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;

/**
 * agent 双向流入口（v3 瘦身：admin 只做通信透传，不落业务库）。
 * - 事件：仅转发 SSE（thinking/delta/tool_call/tool_result/error/plan_update），不落 agent_events
 * - 结果：仅落对话消息 + SSE done（对话通信）；任务行/建议状态由 worker 直写库
 * - plan_update：解析 content → 落一条 assistant 消息 + SSE 通知前端刷新 plan 卡片
 */
@GrpcService
@RequiredArgsConstructor
@Slf4j
public class AgentGrpcService extends AgentServiceGrpc.AgentServiceImplBase {

    private final WorkerRegistry registry;
    private final ToolSchemaService toolSchemaService;
    private final ConversationStreamManager streamManager;
    private final AgentConversationService conversationService;
    private final ObjectMapper objectMapper;

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
                List<ToolSchema> tools = toolSchemaService.readToolSchemas();
                observer.onNext(ServerMessage.newBuilder()
                        .setRegisterAck(RegisterAck.newBuilder()
                                .setOk(true)
                                .setMessage("registered, agents=" + agents.stream()
                                        .map(AgentInfo::getAgentId).collect(Collectors.joining(",")))
                                .addAllTools(tools))
                        .build());
                log.info("register ack sent to {}: {} read tools", register.getWorkerId(), tools.size());
            }

            /** 事件只转发 SSE；plan_update 额外落一条 assistant 消息（对话通信）。 */
            private void handleEvent(TaskEvent event) {
                touch();
                forwardStreamEvent(event);
                if ("plan_update".equals(event.getEventType())) {
                    handlePlanUpdate(event.getContent());
                }
            }

            /** 结果只落对话消息（task/suggestion 状态由 worker 直写库）。 */
            private void handleResult(TaskResult result) {
                touch();
                String conversationId = streamManager.conversationOf(result.getTaskId());
                if (conversationId != null) {
                    conversationService.finishAssistant(conversationId, result.getTaskId(),
                            result.getOk(), result.getConclusion(), result.getReasoning(), result.getError());
                }
            }

            private void handleAgentUpdate(AgentUpdate update) {
                touch();
                String workerId = workerIdRef.get();
                registry.get(workerId).ifPresent(entry -> entry.setAgents(update.getAgentsList()));
            }

            /** plan_update：content JSON {planId,status,summary,message} → 落 assistant 消息 + SSE。 */
            private void handlePlanUpdate(String content) {
                try {
                    Map<?, ?> data = objectMapper.readValue(content, Map.class);
                    String planId = String.valueOf(data.getOrDefault("planId", ""));
                    String status = String.valueOf(data.getOrDefault("status", ""));
                    String summary = String.valueOf(data.getOrDefault("summary", ""));
                    String message = String.valueOf(data.getOrDefault("message", ""));
                    String conversationId = String.valueOf(data.getOrDefault("conversationId", ""));
                    if (conversationId.isBlank() || "null".equals(conversationId)) {
                        log.warn("plan_update without conversationId, ignored: plan={}", planId);
                        return;
                    }
                    String text = buildPlanMessage(status, summary, message);
                    conversationService.savePlanUpdateMessage(conversationId, text, planId);
                    streamManager.push(conversationId, "plan_update",
                            Map.of("planId", planId, "status", status, "message", text));
                    log.info("plan_update handled: plan={} status={} conversation={}",
                            planId, status, conversationId);
                } catch (Exception e) {
                    log.warn("plan_update parse failed: {}", e.getMessage());
                }
            }

            private String buildPlanMessage(String status, String summary, String message) {
                String head = switch (status) {
                    case "DONE" -> "计划已完成";
                    case "FAILED" -> "计划已失败";
                    case "CANCELLED" -> "计划已废弃";
                    case "RUNNING" -> "计划推进中";
                    default -> "计划更新";
                };
                StringBuilder sb = new StringBuilder("**").append(head).append("**");
                if (summary != null && !summary.isBlank() && !"null".equals(summary)) {
                    sb.append("：").append(summary);
                }
                if (message != null && !message.isBlank() && !"null".equals(message)) {
                    sb.append("\n\n").append(message);
                }
                return sb.toString();
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

    /** 把 worker 事件转推给对应会话的 SSE 流（thinking/delta/tool_call/tool_result/error）。 */
    private void forwardStreamEvent(TaskEvent event) {
        String type = event.getEventType();
        String taskId = event.getTaskId();
        switch (type) {
            case "thinking", "delta" -> streamManager.pushByTask(taskId, type,
                    Map.of("delta", event.getContent()));
            case "tool_call", "tool_result" -> streamManager.pushByTask(taskId, type,
                    parseJsonOrRaw(event.getContent()));
            case "error" -> streamManager.pushByTask(taskId, "error",
                    Map.of("message", event.getContent()));
            default -> { /* progress / plan_update 等不走此分支 */ }
        }
    }

    /** tool_call/tool_result 的 content 是 JSON 字符串：能解析则透传对象，否则原样字符串。 */
    private Object parseJsonOrRaw(String content) {
        try {
            return objectMapper.readValue(content, Object.class);
        } catch (Exception e) {
            return content;
        }
    }
}
