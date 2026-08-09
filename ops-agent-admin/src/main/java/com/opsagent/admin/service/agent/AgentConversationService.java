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
import java.util.concurrent.ConcurrentHashMap;

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

    // ===== 多轮 assistant 流内落库（与前端 openStream rotateTurn 对齐）=====
    // 每个 task 维护"当前轮次行"的内存累积；tool_call 事件触发当前轮落库（早于工具行，
    // 保证时间线顺序），下一轮 thinking/delta 新建行。流内只落库已完成轮（tool_call 时）
    // 与最后一轮（done 时），落库次数 = 轮次数（少、性能好），重进顺序与运行中一致。
    private final ConcurrentHashMap<String, AssistantRound> assistantRounds = new ConcurrentHashMap<>();

    /** 单轮 assistant 消息的内存累积（按 messageId upsert 落库） */
    private static final class AssistantRound {
        final String messageId = UUID.randomUUID().toString();
        final StringBuilder reasoning = new StringBuilder();
        final StringBuilder content = new StringBuilder();
        boolean hasData = false;
    }

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
        userMsg.setKind(ConversationMessage.KIND_USER);
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
        AssistantRound round = assistantRounds.get(taskId);
        if (round != null) {
            // 多轮流内已落库：最后一轮用 conclusion 兜底 content，置终态，清理内存状态
            if (conclusion != null && !conclusion.isBlank()) {
                round.content.setLength(0);
                round.content.append(conclusion);
                round.hasData = true;
            }
            persistAssistantRound(conversationId, taskId, round, ok ? STATUS_COMPLETED : STATUS_FAILED);
            assistantRounds.remove(taskId);
            Map<String, Object> done = new LinkedHashMap<>();
            done.put("messageId", round.messageId);
            done.put("status", ok ? STATUS_COMPLETED : STATUS_FAILED);
            done.put("content", round.content.toString());
            done.put("reasoning", round.reasoning.toString());
            done.put("taskId", taskId);
            streamManager.push(conversationId, "done", done);
            streamManager.unbindTask(taskId);
            log.info("conversation assistant rounds finalized: conversation={}, task={}, ok={}",
                    conversationId, taskId, ok);
            return;
        }
        // 兼容：无流内事件（无 worker / 任务失败路径）→ 单条 assistant 消息（原有逻辑）
        ConversationMessage msg = messageRepository.findFirstByTaskId(taskId).orElse(new ConversationMessage());
        boolean isNew = msg.getId() == null;
        if (isNew) {
            msg.setMessageId(UUID.randomUUID().toString());
            msg.setConversationId(conversationId);
            msg.setKind(ConversationMessage.KIND_ASSISTANT);
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

    /**
     * thinking/delta 增量：落到当前 assistant 轮次行；无当前轮则新建。
     * content 为 worker 直发的纯文本 delta（非 JSON）。chunkType: "reasoning" | "content"。
     */
    public void appendAssistantChunk(String taskId, String chunkType, String content) {
        if (taskId == null || taskId.isBlank() || content == null || content.isEmpty()) return;
        String cid = streamManager.conversationOf(taskId);
        if (cid == null || cid.isBlank()) return;
        AssistantRound round = assistantRounds.get(taskId);
        if (round == null) {
            round = new AssistantRound();
            assistantRounds.put(taskId, round);
        }
        if ("reasoning".equals(chunkType)) round.reasoning.append(content);
        else round.content.append(content);
        if (content.trim().length() > 0) round.hasData = true;
    }

    /** tool_call 事件：当前轮推理已完整，落库为 completed（早于工具行，保证时间线顺序）。 */
    public void flushAssistantRoundOnToolCall(String taskId) {
        if (taskId == null || taskId.isBlank()) return;
        String cid = streamManager.conversationOf(taskId);
        AssistantRound round = assistantRounds.get(taskId);
        if (round != null && round.hasData && cid != null && !cid.isBlank()) {
            persistAssistantRound(cid, taskId, round, STATUS_COMPLETED);
        }
        assistantRounds.remove(taskId);
    }

    /** 任务中断（error/超时）：把内存中最后一轮 flush 为 failed，避免丢失已产生的推理上下文。 */
    public void finalizeAssistantOnError(String taskId) {
        if (taskId == null || taskId.isBlank()) return;
        String cid = streamManager.conversationOf(taskId);
        AssistantRound round = assistantRounds.remove(taskId);
        if (round != null && round.hasData && cid != null && !cid.isBlank()) {
            persistAssistantRound(cid, taskId, round, STATUS_FAILED);
        }
    }

    /** 落/更新一行 assistant 消息（按 messageId upsert）。 */
    private void persistAssistantRound(String cid, String taskId, AssistantRound round, String status) {
        try {
            ConversationMessage msg = messageRepository.findFirstByMessageId(round.messageId)
                    .orElseGet(() -> {
                        ConversationMessage m = new ConversationMessage();
                        m.setMessageId(round.messageId);
                        m.setConversationId(cid);
                        m.setKind(ConversationMessage.KIND_ASSISTANT);
                        m.setRole(ROLE_ASSISTANT);
                        return m;
                    });
            msg.setTaskId(taskId);
            msg.setContent(round.content.toString());
            msg.setReasoning(round.reasoning.toString());
            msg.setStatus(status);
            messageRepository.save(msg);
        } catch (Exception e) {
            log.warn("persist assistant round failed (ignored): task={}, err={}", taskId, e.getMessage());
        }
    }

    // ==================== helpers ====================

    // ==================== event-typed messages (timeline persistence) ====================

    /**
     * 落/更新一行 TOOL_CALL 消息：以 callId 作为 messageId 的派生键（IDEMPOTENT upsert）。
     * - 首次 tool_call 事件：插入新行（status=running）
     * - 配对 tool_result 事件到达：原地更新 tool_summary + status=completed
     * - 历史按 callId（一 call 一行）渲染，避免工具调用与结果分散到两条消息上不便阅读
     */
    @Transactional
    public void upsertToolCallRow(String conversationId, String taskId,
                                  String callId, String name, String argsJson,
                                  boolean isResult, String summary) {
        if (conversationId == null || conversationId.isBlank()) return;
        if (callId == null || callId.isBlank()) callId = UUID.randomUUID().toString();
        final String finalCallId = callId;
        try {
            ConversationMessage msg = messageRepository
                    .findFirstByToolCallId(conversationId, callId)
                    .orElseGet(() -> {
                        ConversationMessage m = new ConversationMessage();
                        m.setMessageId("tc:" + finalCallId);
                        m.setConversationId(conversationId);
                        m.setKind(ConversationMessage.KIND_TOOL_CALL);
                        m.setStatus(isResult ? ConversationMessage.STATUS_COMPLETED : "running");
                        return m;
                    });
            msg.setTaskId(taskId);
            msg.setToolCallId(callId);
            msg.setToolName(name);
            if (isResult) {
                msg.setToolSummary(summary == null ? "" : summary);
                msg.setStatus(ConversationMessage.STATUS_COMPLETED);
                msg.setContent(String.format("调用工具 %s · 已返回", name == null ? "tool" : name));
            } else {
                msg.setToolArgs(argsJson);
                msg.setContent(String.format("调用工具 %s", name == null ? "tool" : name));
            }
            messageRepository.save(msg);
        } catch (Exception e) {
            log.warn("upsert tool message failed (ignored): conv={}, callId={}, err={}",
                    conversationId, callId, e.getMessage());
        }
    }

    /**
     * 落/更新 APPROVAL 消息：以 suggestionId 作为 messageId 的派生键（IDEMPOTENT upsert）。
     * 同一建议只占一行：approve/reject/执行结果都在原行上更新 decision 与 payload_json。
     * 历史渲染按 id asc + payload.suggestionId 即可识别，同 suggestionId 只保留一条最新版。
     */
    @Transactional
    public void saveApprovalDecision(String conversationId, String taskId,
                                     String suggestionId, String decision,
                                     String payloadJson) {
        if (conversationId == null || conversationId.isBlank()) return;
        if (suggestionId == null || suggestionId.isBlank()) {
            log.warn("saveApprovalDecision skipped: missing suggestionId, conv={}", conversationId);
            return;
        }
        try {
            ConversationMessage msg = messageRepository
                    .findFirstByPayloadSuggestionId(conversationId, suggestionId)
                    .orElseGet(() -> {
                        ConversationMessage m = new ConversationMessage();
                        m.setMessageId(UUID.randomUUID().toString());
                        m.setConversationId(conversationId);
                        m.setKind(ConversationMessage.KIND_APPROVAL);
                        m.setStatus(ConversationMessage.STATUS_COMPLETED);
                        return m;
                    });
            msg.setTaskId(taskId);
            msg.setDecision(decision);
            msg.setPayloadJson(payloadJson);
            msg.setContent(buildApprovalSummary(payloadJson, decision));
            messageRepository.save(msg);
        } catch (Exception e) {
            log.warn("save approval message failed (ignored): conv={}, sug={}, err={}",
                    conversationId, suggestionId, e.getMessage());
        }
    }

    /** 从 payload JSON 提取 actionType/decision/target 拼一行简短摘要（前端气泡副标题）。 */
    private String buildApprovalSummary(String payloadJson, String decision) {
        String action = "";
        String target = "";
        if (payloadJson != null && !payloadJson.isBlank()) {
            try {
                Map<String, Object> data = objectMapper.readValue(payloadJson, Map.class);
                action = String.valueOf(data.getOrDefault("actionType", ""));
                Object tt = data.get("targetType");
                Object ti = data.get("targetId");
                if (tt != null) {
                    target = (ti == null || String.valueOf(ti).isBlank() || "0".equals(String.valueOf(ti)))
                            ? String.valueOf(tt) : tt + ":" + ti;
                }
            } catch (Exception ignored) {
                // 解析失败不影响消息写入
            }
        }
        String head = switch (decision) {
            case "APPROVED" -> "已授权";
            case "REJECTED" -> "已忽略";
            case "EXECUTING" -> "执行中";
            case "EXECUTED" -> "已执行";
            case "FAILED" -> "执行失败";
            case "EXPIRED" -> "已过期";
            default -> "待审批";
        };
        if (action.isBlank()) return head;
        return target.isBlank() ? head + " · " + action : head + " · " + action + " (" + target + ")";
    }

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
        msg.setKind(ConversationMessage.KIND_ASSISTANT);
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
            String role = m.getRole();
            // 新消息用 kind 区分；历史用 role 区分（kind 字段新增前的老消息 role=user/assistant/system）
            boolean isUserLike = ConversationMessage.KIND_USER.equals(m.getKind()) || "user".equals(role);
            boolean isAssistantLike = ConversationMessage.KIND_ASSISTANT.equals(m.getKind())
                    || "assistant".equals(role);
            if (!isUserLike && !isAssistantLike) {
                continue;
            }
            String content = m.getContent();
            if (content == null || content.isBlank()) {
                continue;
            }
            Map<String, String> item = new LinkedHashMap<>();
            item.put("role", isUserLike ? "user" : "assistant");
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
