package com.opsagent.admin.service.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.entity.AgentSuggestion;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.entity.Conversation;
import com.opsagent.admin.entity.ConversationMessage;
import com.opsagent.admin.repository.AgentSuggestionRepository;
import com.opsagent.admin.repository.AgentTaskRepository;
import com.opsagent.admin.repository.AgentConversationRepository;
import com.opsagent.admin.repository.AgentPlanRepository;
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
    private final AgentPlanRepository agentPlanRepository;
    private final ConversationMessageRepository messageRepository;
    private final AgentTaskService taskService;
    private final ConversationStreamManager streamManager;
    private final ObjectMapper objectMapper;
    private final AgentTaskRepository taskRepository;
    private final AgentSuggestionRepository suggestionRepository;

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
                                    String taskType, String targetType, Long targetId,
                                    boolean reasoningEnabled) {
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

        AgentTask task = taskService.dispatchChat(
                conv.getConversationId(), text, history, targetType, targetId, userId,
                reasoningEnabled);
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
     * execute 成功后自动派发 continue 任务：复用决策轮推进 plan 步骤。
     * 仅当 suggestion 关联了 plan（plan_id 非空）才触发，避免对单步建议空跑决策。
     * 由 AgentGrpcService.handleResult 在 TaskResult 到达时调用。
     */
    public void autoContinueForPlanStep(String conversationId, String taskId,
                                        String conclusion) {
        if (conversationId == null || conversationId.isBlank()) return;
        // 1. taskId 反查 suggestion_id
        java.util.Optional<AgentTask> taskOpt = taskRepository.findByTaskId(taskId);
        if (taskOpt.isEmpty()) {
            log.info("autoContinue skipped: task not found: {}", taskId);
            return;
        }
        String suggestionId = taskOpt.get().getSuggestionId();
        if (suggestionId == null || suggestionId.isBlank()) {
            log.info("autoContinue skipped: task has no suggestion_id: {}", taskId);
            return;
        }
        // 2. suggestionId 查 plan_id
        java.util.Optional<AgentSuggestion> sugOpt = suggestionRepository.findBySuggestionId(suggestionId);
        if (sugOpt.isEmpty()) {
            log.debug("autoContinue skipped: suggestion not found: {}", suggestionId);
            return;
        }
        AgentSuggestion suggestion = sugOpt.get();
        String planId = suggestion.getPlanId();
        if (planId == null || planId.isBlank()) {
            log.debug("autoContinue skipped: suggestion has no plan_id: {}", suggestionId);
            return;
        }
        // 1. 只对 execute 任务派 continue；continue 任务完成不要再触发新的 continue（防递归死循环）
        if (!"execute".equals(taskOpt.get().getTaskType())) {
            log.debug("autoContinue skipped: task type={} (only execute triggers)",
                    taskOpt.get().getTaskType());
            return;
        }
        // 2. plan 终态（已完成/失败/取消）也不再触发
        if (agentPlanRepository.findByPlanId(planId).map(p -> {
            String s = p.getStatus();
            return "DONE".equals(s) || "FAILED".equals(s) || "CANCELLED".equals(s);
        }).orElse(false)) {
            log.info("autoContinue skipped: plan {} already terminal", planId);
            return;
        }
        // 3. 派 continue 任务
        try {
            taskService.dispatchContinuePlanStep(conversationId, planId,
                    suggestion.getStepNo(), suggestionId,
                    conclusion == null ? "" : conclusion,
                    null);  // execute 任务行未记录触发人，fallback 到 read-only 权限
            log.info("autoContinue dispatched: plan={} step={} conv={}",
                    planId, suggestion.getStepNo(), conversationId);
        } catch (Exception e) {
            log.warn("autoContinue dispatch failed: plan={} step={}: {}",
                    planId, suggestion.getStepNo(), e.getMessage());
        }
    }

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
        // 不主动 complete：连接由前端收到 done 后主动 abort 关闭（服务端主动 complete 的
        // 时序会让部分 HTTP 客户端把正常收尾误报为 network error）；断连/超时由
        // SseEmitter 的 onCompletion/onError/onTimeout 及 register 顶掉旧流兜底清理。
        streamManager.unbindTask(taskId);
        log.info("conversation assistant message saved: conversation={}, task={}, ok={}",
                conversationId, taskId, ok);
    }

    // ==================== helpers ====================

    /**
     * plan_update 事件落一条 assistant 消息（agent 直写库后经 gRPC 上报，用户可见 plan 进度变更）。
     * 非对话通信收口（不回 done 事件，仅落库 + 前端自行刷新 plan 卡片）。
     */
    @Transactional
    public void savePlanUpdateMessage(String conversationId, String message, String planId) {
        if (conversationId == null || conversationId.isBlank()) {
            return;
        }
        ConversationMessage msg = new ConversationMessage();
        msg.setMessageId(UUID.randomUUID().toString());
        msg.setConversationId(conversationId);
        msg.setRole(ROLE_ASSISTANT);
        msg.setContent(message == null || message.isBlank() ? "（计划状态更新）" : message);
        msg.setStatus(STATUS_COMPLETED);
        msg.setTaskId(planId == null || planId.isBlank() ? null : planId);
        messageRepository.save(msg);
        log.info("plan update message saved: conversation={}, plan={}", conversationId, planId);
    }

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
