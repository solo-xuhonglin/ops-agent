package com.opsagent.admin.service.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.entity.Conversation;
import com.opsagent.admin.entity.ConversationMessage;
import com.opsagent.admin.repository.ConversationMessageRepository;
import com.opsagent.admin.repository.ConversationRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Agent 多轮会话：conversation + message 持久化、历史组装、消息发起（内部 task 派发）、SSE 收口。
 * 每轮用户提问 = 一条 user message + 一条内部 task（assistant 结果由 AgentGrpcService.complete 落库）。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class AgentConversationService {

    public static final String ROLE_USER = "user";
    public static final String ROLE_ASSISTANT = "assistant";
    public static final String STATUS_COMPLETED = "completed";
    public static final String STATUS_STREAMING = "streaming";
    public static final String STATUS_FAILED = "failed";

    /** 组装多轮上下文时最多携带的历史消息条数（超出按最新的截断） */
    private static final int HISTORY_LIMIT = 20;

    private final ConversationRepository conversationRepository;
    private final ConversationMessageRepository messageRepository;
    private final AgentTaskService taskService;
    private final ConversationStreamManager streamManager;
    private final ObjectMapper objectMapper;

    // ==================== conversation CRUD ====================

    @Transactional
    public Conversation create(Long userId) {
        Conversation conv = new Conversation();
        conv.setConversationId(UUID.randomUUID().toString());
        conv.setTitle("新对话");
        conv.setUserId(userId);
        conversationRepository.save(conv);
        log.info("conversation created: id={}, userId={}", conv.getConversationId(), userId);
        return conv;
    }

    /** 会话列表（新→旧）；非 admin 只能看自己的会话 */
    public Page<Conversation> list(Long userId, int page, int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "updatedAt"));
        if (userId == null) {
            return conversationRepository.findAllByOrderByUpdatedAtDesc(pageable);
        }
        return conversationRepository.findByUserIdOrderByUpdatedAtDesc(userId, pageable);
    }

    @Transactional(readOnly = true)
    public List<ConversationMessage> messages(String conversationId, Long userId) {
        Conversation conv = requireOwned(conversationId, userId);
        return messageRepository.findByConversationIdOrderByIdAsc(conv.getConversationId());
    }

    @Transactional
    public void delete(String conversationId, Long userId) {
        Conversation conv = requireOwned(conversationId, userId);
        streamManager.close(conv.getConversationId());
        messageRepository.deleteByConversationId(conv.getConversationId());
        conversationRepository.delete(conv);
        log.info("conversation deleted: id={}", conversationId);
    }

    // ==================== send message (one round) ====================

    /**
     * 发一条用户消息：落 user message → 组装历史 → 派发内部 task（携带 history）→
     * 绑定 task→conversation 供事件回推 → 返回 {userMessage, taskId, status}。
     * 无在线 worker 时任务直接 FAILED，这里同步落 assistant failed 消息并推 error 事件。
     */
    @Transactional
    public Map<String, Object> send(String conversationId, Long userId, String query,
                                    String taskType, String targetType, Long targetId) {
        Conversation conv = requireOwned(conversationId, userId);
        String text = (query == null || query.isBlank()) ? "" : query.trim();
        if (text.isBlank() && targetId == null) {
            throw new IllegalArgumentException("消息内容不能为空");
        }

        // 先基于旧消息组装多轮历史（不含本轮），再落 user 消息，避免本轮问题重复进入上下文
        String history = buildHistory(conv.getConversationId());

        ConversationMessage userMsg = new ConversationMessage();
        userMsg.setMessageId(UUID.randomUUID().toString());
        userMsg.setConversationId(conv.getConversationId());
        userMsg.setRole(ROLE_USER);
        userMsg.setContent(text);
        userMsg.setStatus(STATUS_COMPLETED);
        messageRepository.save(userMsg);

        // 首条消息且标题未定制 → 取前 20 字做标题
        if ("新对话".equals(conv.getTitle()) && !text.isBlank()) {
            conv.setTitle(text.length() > 20 ? text.substring(0, 20) : text);
            conversationRepository.save(conv);
        }

        AgentTask task = taskService.dispatch(
                taskType == null || taskType.isBlank() ? "question" : taskType,
                targetType, targetId, text, userId, history);
        streamManager.bindTask(task.getTaskId(), conv.getConversationId());

        if (AgentTaskService.STATUS_FAILED.equals(task.getStatus())) {
            // 无 worker：立即收敛为 failed assistant 消息并推 error，前端不用干等
            finishAssistant(conv.getConversationId(), task.getTaskId(), false,
                    task.getConclusion(), null, "no agent worker online");
            log.warn("conversation message failed to dispatch: conversation={}, task={}",
                    conversationId, task.getTaskId());
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("messageId", userMsg.getMessageId());
        result.put("taskId", task.getTaskId());
        result.put("status", task.getStatus());
        return result;
    }

    // ==================== assistant result (called from AgentGrpcService) ====================

    /**
     * 内部任务完成：落 assistant 消息（completed/failed）并推 SSE done 事件、解绑 task。
     * 非会话任务（execute_suggestion 等）无关联会话，直接忽略。
     */
    @Transactional
    public void finishAssistant(String conversationId, String taskId, boolean ok,
                                String conclusion, String reasoning, String error) {
        if (conversationId == null || conversationId.isBlank()) {
            streamManager.unbindTask(taskId);
            return;
        }
        ConversationMessage msg = messageRepository.findFirstByTaskId(taskId).orElse(new ConversationMessage());
        boolean isNew = msg.getId() == null;
        if (isNew) {
            msg.setMessageId(UUID.randomUUID().toString());
            msg.setConversationId(conversationId);
            msg.setRole(ROLE_ASSISTANT);
        }
        msg.setTaskId(taskId);
        msg.setContent(conclusion == null || conclusion.isBlank()
                ? (error == null || error.isBlank() ? "（空回复）" : error) : conclusion);
        msg.setReasoning(reasoning);
        msg.setStatus(ok ? STATUS_COMPLETED : STATUS_FAILED);
        messageRepository.save(msg);

        Map<String, Object> done = new LinkedHashMap<>();
        done.put("messageId", msg.getMessageId());
        done.put("status", msg.getStatus());
        done.put("content", msg.getContent());
        done.put("reasoning", msg.getReasoning());
        done.put("taskId", taskId);
        streamManager.push(conversationId, "done", done);
        streamManager.unbindTask(taskId);
        log.info("conversation assistant message saved: conversation={}, task={}, ok={}",
                conversationId, taskId, ok);
    }

    // ==================== helpers ====================

    /** 组装多轮历史：最近 HISTORY_LIMIT 条已完成 user/assistant 消息，JSON 数组（新→旧取前 N 再反转）。 */
    private String buildHistory(String conversationId) {
        List<ConversationMessage> recent = messageRepository
                .findByConversationIdAndStatusInOrderByIdDesc(conversationId,
                        List.of(STATUS_COMPLETED))
                .stream().limit(HISTORY_LIMIT).toList();
        List<Map<String, String>> history = new ArrayList<>();
        for (int i = recent.size() - 1; i >= 0; i--) {
            ConversationMessage m = recent.get(i);
            if (!ROLE_USER.equals(m.getRole()) && !ROLE_ASSISTANT.equals(m.getRole())) {
                continue;
            }
            String content = m.getContent();
            if (content == null || content.isBlank()) {
                continue;
            }
            Map<String, String> item = new LinkedHashMap<>();
            item.put("role", m.getRole());
            item.put("content", content);
            history.add(item);
        }
        if (history.isEmpty()) {
            return "";
        }
        try {
            return objectMapper.writeValueAsString(history);
        } catch (Exception e) {
            log.warn("build history json failed, send without history: conversation={}", conversationId, e);
            return "";
        }
    }

    /** 校验会话归属（userId 为空视为系统内部，跳过归属校验）。 */
    private Conversation requireOwned(String conversationId, Long userId) {
        Conversation conv = conversationRepository.findByConversationId(conversationId)
                .orElseThrow(() -> new ResourceNotFoundException("会话不存在: " + conversationId));
        if (userId != null && conv.getUserId() != null && !conv.getUserId().equals(userId)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.FORBIDDEN, "无权访问该会话");
        }
        return conv;
    }
}
