package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.CancelTask;
import com.opsagent.admin.agent.proto.ServerMessage;
import com.opsagent.admin.agent.proto.TaskDispatch;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.entity.Permission;
import com.opsagent.admin.entity.Role;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.AgentTaskRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.JwtUtil;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * AI Agent 任务派发与查询（admin 只做通信）。
 * - 任务行由 worker 直写 agent_tasks；admin 不再维护状态机/事件落库/超时扫描（agent 自治）
 * - 本类只负责：生成 taskId → 组装 TaskDispatch → 事务提交后沿 gRPC 流 push
 * - 无在线 worker 时返回 FAILED 状态（由调用方转 failed 消息）
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class AgentTaskService {

    public static final String STATUS_DISPATCHED = "DISPATCHED";
    public static final String STATUS_RUNNING = "RUNNING";
    public static final String STATUS_SUCCEEDED = "SUCCEEDED";
    public static final String STATUS_FAILED = "FAILED";
    public static final String STATUS_CANCELLED = "CANCELLED";

    /** 任务级 scoped token 有效期：5 分钟（诊断任务足够，过期即失效） */
    public static final long SCOPED_TOKEN_TTL_MS = 5 * 60_000;

    /** 执行已审批写操作（execute）的任务 token 有效期：覆盖异步跟踪期（训练/部署可能数小时），可配置 */
    @Value("${agent.execute-token-ttl-seconds:86400}")
    private long executeTokenTtlSeconds;

    /**
     * fallback 权限：当 dispatchedBy 为空/用户已删除时签发，避免 agent 拿到 token 但用户表查不到。
     * 给业务只读权限——这是降级路径，正常路径总是用用户完整权限。
     */
    private static final List<String> FALLBACK_READ_PERMISSIONS = List.of(
            "dataset:read", "model:read", "training:read", "serving:read");

    private final AgentTaskRepository taskRepository;
    private final WorkerRegistry workerRegistry;
    private final UserRepository userRepository;
    private final JwtUtil jwtUtil;

    /** 从 userId 解析完整权限列表（roles → permissions → 去重 code），用于签发 scoped token。 */
    private List<String> resolveFullPermissions(Long userId) {
        if (userId == null) {
            return FALLBACK_READ_PERMISSIONS;
        }
        Optional<User> user = userRepository.findById(userId);
        if (user.isEmpty() || user.get().getRoles() == null || user.get().getRoles().isEmpty()) {
            log.warn("scoped token fallback to read-only: userId={} not found or no roles", userId);
            return FALLBACK_READ_PERMISSIONS;
        }
        Set<String> codes = new LinkedHashSet<>();
        for (Role role : user.get().getRoles()) {
            if (role.getPermissions() == null) continue;
            for (Permission p : role.getPermissions()) {
                if (p.getCode() != null && !p.getCode().isBlank()) {
                    codes.add(p.getCode());
                }
            }
        }
        if (codes.isEmpty()) {
            return FALLBACK_READ_PERMISSIONS;
        }
        return List.copyOf(codes);
    }

    /**
     * 派发对话轮（chat）任务：生成 taskId + TaskDispatch，事务提交后 push。
     * 不落 agent_tasks（worker 收到后自写）；无在线 worker 返回 FAILED 状态（内存对象）。
     */
    @Transactional
    public AgentTask dispatchChat(String conversationId, String query, String history,
                                  String targetType, Long targetId, Long dispatchedBy,
                                  boolean reasoningEnabled) {
        String taskId = UUID.randomUUID().toString();
        AgentTask task = buildPending(taskId, "chat", conversationId, query);
        WorkerRegistry.WorkerEntry worker = workerRegistry.all().stream().findFirst().orElse(null);
        if (worker == null) {
            task.setStatus(STATUS_FAILED);
            task.setConclusion("no agent worker online");
            log.warn("chat dispatch skipped, no online worker: conversation={}", conversationId);
            return task;
        }
        String taskToken = jwtUtil.generateScopedToken(dispatchedBy,
                resolveFullPermissions(dispatchedBy), taskId, SCOPED_TOKEN_TTL_MS);
        TaskDispatch.Builder b = TaskDispatch.newBuilder()
                .setTaskId(taskId)
                .setTaskType("chat")
                .setQuery(query == null ? "" : query)
                .setTaskToken(taskToken)
                .setTargetType(targetType == null ? "" : targetType)
                .setTargetId(targetId == null ? 0 : targetId)
                .setReasoningEnabled(reasoningEnabled);
        if (conversationId != null && !conversationId.isBlank()) {
            b.setConversationId(conversationId);
        }
        if (history != null && !history.isBlank()) {
            b.setHistory(history);
        }
        pushAfterCommit(worker, taskId, ServerMessage.newBuilder().setTaskDispatch(b).build());
        return task;
    }

    /**
     * 派发执行（execute）任务：approve 后调用；grant_key 随 TaskDispatch 下发（替代单独 AuthorizationGrant 推送）。
     * 不落 agent_tasks（worker 收到后自写，suggestion_id 关联）。
     */
    @Transactional
    public AgentTask dispatchExecute(String conversationId, String suggestionId,
                                     String actionType, String targetType, Long targetId,
                                     String params, String grantKey, Long confirmedBy) {
        String taskId = UUID.randomUUID().toString();
        AgentTask task = buildPending(taskId, "execute", conversationId, null);
        task.setSuggestionId(suggestionId);
        WorkerRegistry.WorkerEntry worker = workerRegistry.all().stream().findFirst().orElse(null);
        if (worker == null) {
            task.setStatus(STATUS_FAILED);
            task.setConclusion("no agent worker online");
            log.warn("execute dispatch skipped, no online worker: suggestion={}", suggestionId);
            return task;
        }
        // 长 TTL：覆盖异步跟踪期（worker 后台轮询训练/部署对象状态）
        long ttlMs = executeTokenTtlSeconds * 1000;
        String taskToken = jwtUtil.generateScopedToken(confirmedBy,
                resolveFullPermissions(confirmedBy), taskId, ttlMs);
        TaskDispatch.Builder b = TaskDispatch.newBuilder()
                .setTaskId(taskId)
                .setTaskType("execute")
                .setTaskToken(taskToken)
                .setSuggestionId(suggestionId == null ? "" : suggestionId)
                .setActionType(actionType == null ? "" : actionType)
                .setTargetType(targetType == null ? "" : targetType)
                .setTargetId(targetId == null ? 0 : targetId)
                .setParams(params == null ? "{}" : params)
                .setGrantKey(grantKey == null ? "" : grantKey);
        if (conversationId != null && !conversationId.isBlank()) {
            b.setConversationId(conversationId);
        }
        pushAfterCommit(worker, taskId, ServerMessage.newBuilder().setTaskDispatch(b).build());
        return task;
    }

    /** 用户/前端取消：发 CancelTask（worker 自治置 CANCELLED 并回写关联状态），admin 不落库。 */
    public void cancel(String taskId, String reason) {
        WorkerRegistry.WorkerEntry worker = workerRegistry.all().stream().findFirst().orElse(null);
        if (worker == null) {
            log.warn("cancel skipped, no online worker: taskId={}", taskId);
            return;
        }
        try {
            worker.getResponseObserver().onNext(ServerMessage.newBuilder()
                    .setCancelTask(CancelTask.newBuilder()
                            .setTaskId(taskId)
                            .setReason(reason == null ? "cancelled by user" : reason))
                    .build());
            log.info("cancel sent: taskId={} reason={}", taskId, reason);
        } catch (Exception e) {
            log.warn("cancel send failed: taskId={}", taskId, e);
        }
    }

    private AgentTask buildPending(String taskId, String taskType, String conversationId, String query) {
        AgentTask task = new AgentTask();
        task.setTaskId(taskId);
        task.setTaskType(taskType);
        task.setConversationId(conversationId);
        task.setQuery(query);
        task.setStatus(STATUS_DISPATCHED);
        return task;
    }

    /** 推送放到事务提交后：worker 秒回时其写库事务能看到本任务上下文（避免竞态静默丢失）。 */
    private void pushAfterCommit(WorkerRegistry.WorkerEntry worker, String taskId, ServerMessage msg) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            push(worker, taskId, msg);
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                push(worker, taskId, msg);
            }
        });
    }

    private void push(WorkerRegistry.WorkerEntry worker, String taskId, ServerMessage msg) {
        try {
            worker.getResponseObserver().onNext(msg);
            log.info("task dispatched: taskId={} type={}", taskId,
                    msg.getTaskDispatch().getTaskType());
        } catch (Exception e) {
            // 事务已提交；worker 失联，任务行由 worker 侧不存在——仅记录，前端靠超时/重试兜底
            log.error("dispatch send failed: taskId={}", taskId, e);
        }
    }

    // ==================== 只读查询 ====================

    public Page<AgentTask> list(int page, int size) {
        return taskRepository.findAllByOrderByIdDesc(
                PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "id")));
    }

    public Optional<AgentTask> get(String taskId) {
        return taskRepository.findByTaskId(taskId);
    }

    public List<AgentTask> byConversation(String conversationId) {
        return taskRepository.findByConversationIdOrderByIdDesc(conversationId);
    }
}
