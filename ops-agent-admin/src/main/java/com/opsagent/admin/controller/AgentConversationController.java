package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.AgentMessageRequest;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.entity.Conversation;
import com.opsagent.admin.entity.ConversationMessage;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import com.opsagent.admin.service.agent.AgentConversationService;
import com.opsagent.admin.service.agent.AgentTaskService;
import com.opsagent.admin.service.agent.ConversationStreamManager;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Agent 多轮对话管理面 API（人用）：会话 CRUD + 历史恢复 + 发消息 + SSE 流式回显。
 * 发消息走内部 task 派发（复用授权闭环），worker 事件经 ConversationStreamManager 转 SSE。
 */
@RestController
@RequestMapping("/api/agent/conversations")
@RequiredArgsConstructor
public class AgentConversationController {

    private final AgentConversationService conversationService;
    private final AgentTaskService taskService;
    private final ConversationStreamManager streamManager;
    private final UserRepository userRepository;
    private final CurrentUser currentUser;

    @PostMapping
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> create() {
        return ApiResponse.ok(conversationService.create(resolveUserId()));
    }

    @GetMapping
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        Page<Conversation> result = conversationService.list(resolveUserId(), page, size);
        return ApiResponse.ok(result);
    }

    /** 历史恢复：完整消息流（新→旧排列由前端决定，这里返回时间升序）。 */
    @GetMapping("/{conversationId}/messages")
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> messages(@PathVariable String conversationId) {
        List<ConversationMessage> messages = conversationService.messages(conversationId, resolveUserId());
        return ApiResponse.ok(messages);
    }

    @DeleteMapping("/{conversationId}")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> delete(@PathVariable String conversationId) {
        conversationService.delete(conversationId, resolveUserId());
        return ApiResponse.ok();
    }

    /** 发消息：建 user message + 派发内部 task（带多轮 history），返回 {messageId, taskId, status}。 */
    @PostMapping("/{conversationId}/messages")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> send(@PathVariable String conversationId,
                               @Valid @RequestBody AgentMessageRequest req) {
        Map<String, Object> result = conversationService.send(conversationId, resolveUserId(),
                req.getQuery(), req.getTaskType(), req.getTargetType(), req.getTargetId(),
                Boolean.TRUE.equals(req.getReasoning()));
        return ApiResponse.ok(result);
    }

    /**
     * SSE 流式回显：注册会话流后，worker 事件（thinking/delta/tool_call/tool_result/done/error）
     * 实时推给前端。taskId 可选：若该轮任务已完成而事件已错过（重连/秒回），回放最终 done/error 兜底。
     */
    @PostMapping(value = "/{conversationId}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @PreAuthorize("hasAuthority('agent:read')")
    public SseEmitter stream(@PathVariable String conversationId,
                             @RequestParam(required = false) String taskId) {
        // 归属校验（抛 403 则连接不进流）
        List<ConversationMessage> messages = conversationService.messages(conversationId, resolveUserId());
        SseEmitter emitter = streamManager.register(conversationId);
        replayIfFinished(conversationId, taskId, messages);
        return emitter;
    }

    /** 任务已结束（SUCCEEDED/FAILED/CANCELLED）时，把最终 done/error 事件回放给迟到的 SSE 连接。 */
    private void replayIfFinished(String conversationId, String taskId, List<ConversationMessage> messages) {
        if (taskId == null || taskId.isBlank()) {
            return;
        }
        taskService.get(taskId).ifPresent(task -> {
            String status = task.getStatus();
            if (AgentTaskService.STATUS_SUCCEEDED.equals(status)
                    || AgentTaskService.STATUS_FAILED.equals(status)
                    || AgentTaskService.STATUS_CANCELLED.equals(status)) {
                // 从会话消息库取该轮的 assistant 消息（若无则按任务状态回放 error）
                ConversationMessage assistant = messages.stream()
                        .filter(m -> taskId.equals(m.getTaskId())).findFirst().orElse(null);
                if (assistant != null) {
                    Map<String, Object> done = new LinkedHashMap<>();
                    done.put("messageId", assistant.getMessageId());
                    done.put("status", assistant.getStatus());
                    done.put("content", assistant.getContent());
                    done.put("reasoning", assistant.getReasoning());
                    done.put("taskId", taskId);
                    streamManager.push(conversationId, "done", done);
                } else {
                    streamManager.push(conversationId, "error",
                            Map.of("message", "task " + status.toLowerCase() + ": " + task.getConclusion()));
                }
                // 不主动 close：前端收到收尾事件后主动 abort 关闭（理由同 finishAssistant）
            }
        });
    }

    private Long resolveUserId() {
        String username = currentUser.username();
        if (username == null) {
            return null;
        }
        return userRepository.findByUsername(username).map(u -> u.getId()).orElse(null);
    }
}
