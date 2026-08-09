package com.opsagent.admin.service.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

/**
 * 会话 SSE 流注册中心：conversationId -> SseEmitter，把 worker 侧 gRPC 事件
 * 转推给等待该会话流式回显的 HTTP 客户端。
 * 同时维护 taskId -> conversationId 映射，供 AgentGrpcService 收事件时定位目标流。
 * 单个会话只允许一个活跃流（新连接顶掉旧的，避免多标签页事件串流）。
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class ConversationStreamManager {

    private final ObjectMapper objectMapper;

    private final Map<String, SseEmitter> streams = new ConcurrentHashMap<>();
    private final Map<String, String> taskConversations = new ConcurrentHashMap<>();

    /**
     * 注册会话流。返回 emitter；调用方负责设置超时与完成回调。
     * 若已有活跃流（并发连接），先完成旧流再注册新流。
     */
    public SseEmitter register(String conversationId) {
        SseEmitter old = streams.remove(conversationId);
        if (old != null) {
            try {
                old.complete();
            } catch (Exception e) {
                log.debug("close stale stream for conversation {}: {}", conversationId, e.getMessage());
            }
        }
        SseEmitter emitter = new SseEmitter(300_000L); // 5min：LLM 流式生成可能较长，0L 会退化为容器默认 30s 超时
        emitter.onCompletion(() -> {
            streams.remove(conversationId);
            log.debug("sse stream completed: conversation={}", conversationId);
        });
        emitter.onTimeout(() -> {
            streams.remove(conversationId);
            log.debug("sse stream timed out: conversation={}", conversationId);
        });
        emitter.onError(t -> {
            streams.remove(conversationId);
            log.debug("sse stream error: conversation={}, err={}", conversationId, t.getMessage());
        });
        streams.put(conversationId, emitter);
        return emitter;
    }

    /** 事件归属：任务 → 会话。任务结束（成功/失败）后移除映射，避免泄漏。 */
    public void bindTask(String taskId, String conversationId) {
        taskConversations.put(taskId, conversationId);
    }

    public void unbindTask(String taskId) {
        taskConversations.remove(taskId);
    }

    /** 查询任务所属会话（未绑定/已解绑返回 null）。 */
    public String conversationOf(String taskId) {
        return taskConversations.get(taskId);
    }

    /**
     * 向会话流推一个 SSE 事件（event name + JSON data）。
     * worker 事件（thinking/delta/tool_call/tool_result/done/error）统一走这里。
     */
    public void push(String conversationId, String event, Object data) {
        SseEmitter emitter = streams.get(conversationId);
        if (emitter == null) {
            return;
        }
        try {
            emitter.send(SseEmitter.event().name(event)
                    .data(objectMapper.writeValueAsString(data)));
        } catch (IOException | IllegalStateException e) {
            log.debug("sse push failed, closing stream: conversation={}, event={}, err={}",
                    conversationId, event, e.getMessage());
            streams.remove(conversationId);
            try {
                emitter.completeWithError(e);
            } catch (Exception ignored) {
                // already closed
            }
        }
    }

    /** 根据任务定位会话并推送事件（AgentGrpcService 收 gRPC 事件时调用）。 */
    public void pushByTask(String taskId, String event, Object data) {
        String conversationId = taskConversations.get(taskId);
        if (conversationId != null) {
            push(conversationId, event, data);
        }
    }

    /** 主动完成会话流（如删除会话时），并解绑其下任务。 */
    public void close(String conversationId) {
        SseEmitter emitter = streams.remove(conversationId);
        if (emitter != null) {
            try {
                emitter.complete();
            } catch (Exception e) {
                log.debug("close stream error: conversation={}", conversationId);
            }
        }
        taskConversations.entrySet().removeIf(e -> e.getValue().equals(conversationId));
    }

    /** 完成会话流但保留 task 映射：done/error 收尾事件推送后调用，避免连接悬挂到超时。
     * 与 close() 的区别：不清 task→conv 绑定（用户可立刻发起下一轮，无需重新绑定）。 */
    public void completeStream(String conversationId) {
        SseEmitter emitter = streams.remove(conversationId);
        if (emitter != null) {
            try {
                emitter.complete();
            } catch (Exception e) {
                log.debug("complete stream error: conversation={}", conversationId);
            }
        }
    }

    /** 供内部使用：若无副作用需执行可忽略；保留以防后续需要遍历。 */
    public void forEachStream(Consumer<SseEmitter> action) {
        streams.values().forEach(action);
    }
}
