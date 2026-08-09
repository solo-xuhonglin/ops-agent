package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.AuthorizationGrant;
import com.opsagent.admin.agent.proto.ServerMessage;
import com.opsagent.admin.entity.AgentSuggestion;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.repository.AgentSuggestionRepository;
import com.opsagent.admin.repository.AgentTaskRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 处置建议闭环：建议落库(PENDING) → 人工确认(approve)签发 grantKey 推 agent → 忽略(reject)。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class AgentSuggestionService {

    private final AgentSuggestionRepository suggestionRepository;
    private final AgentTaskRepository taskRepository;
    private final WorkerRegistry workerRegistry;
    private final GrantService grantService;
    private final AgentTaskService taskService;

    public Page<AgentSuggestion> list(int page, int size) {
        return suggestionRepository.findAllByOrderByIdDesc(
                PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "id")));
    }

    /**
     * 确认建议：签发 grantKey（TTL）→ 落库 APPROVED → 沿 gRPC 流推 AuthorizationGrant 给 agent。
     * worker 离线时不签发，建议保持 PENDING 并抛 IllegalArgumentException（由 Controller 转提示）。
     */
    @Transactional
    public AgentSuggestion approve(Long id, Long confirmedBy) {
        AgentSuggestion suggestion = suggestionRepository.findById(id)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("建议不存在: " + id));
        if (!"PENDING".equals(suggestion.getStatus())) {
            throw new IllegalArgumentException("仅 PENDING 状态的建议可确认，当前: " + suggestion.getStatus());
        }
        WorkerRegistry.WorkerEntry worker = resolveWorker(suggestion);
        if (worker == null) {
            throw new IllegalArgumentException("agent 离线，无法下发授权，建议保持待确认");
        }

        String grantKey = grantService.issue(suggestion.getActionType(), suggestion.getTargetType(),
                suggestion.getTargetId(), suggestion.getId());
        suggestion.setStatus("APPROVED");
        suggestion.setGrantKey(grantKey);
        suggestion.setConfirmedBy(confirmedBy);
        suggestion.setConfirmedAt(OffsetDateTime.now());
        suggestionRepository.save(suggestion);

        worker.getResponseObserver().onNext(ServerMessage.newBuilder()
                .setAuthorizationGrant(AuthorizationGrant.newBuilder()
                        .setActionType(suggestion.getActionType())
                        .setTargetType(suggestion.getTargetType())
                        .setTargetId(suggestion.getTargetId())
                        .setGrantKey(grantKey)
                        .setTtlSeconds((int) grantService.ttlSeconds()))
                .build());

        // 派发"执行建议"任务：grantKey 已在 agent 侧 GrantStore，LLM 按 query（含 target）调写工具（自动带 key）；
        // 带 conversationId（执行结果写回对话）+ suggestionId（长 TTL token + agent 判断已授权）
        String query = "{\"suggestionId\":%d,\"action\":\"%s\",\"targetType\":\"%s\",\"targetId\":%d,\"params\":%s}"
                .formatted(suggestion.getId(), suggestion.getActionType(),
                        suggestion.getTargetType(), suggestion.getTargetId(),
                        suggestion.getParams() == null ? "{}" : suggestion.getParams());
        taskService.dispatch("execute_suggestion", suggestion.getTargetType(),
                suggestion.getTargetId(), query, confirmedBy, null,
                suggestion.getConversationId(), suggestion.getId());

        log.info("suggestion approved: id={} grantKey={} worker={}, execute task dispatched",
                id, grantKey, worker.getWorkerId());
        return suggestion;
    }

    /** agent 侧 Plan 推进时异步上报的建议（gRPC AsyncSuggestion）→ 落 PENDING，用户审批后走 execute_suggestion。 */
    @Transactional
    public AgentSuggestion persistAsync(com.opsagent.admin.agent.proto.AsyncSuggestion proto) {
        if (proto.getConversationId() == null || proto.getConversationId().isBlank()) {
            log.warn("async suggestion ignored: no conversation, action={}", proto.getActionType());
            return null;
        }
        AgentSuggestion suggestion = new AgentSuggestion();
        suggestion.setTaskId(proto.getTaskId());
        suggestion.setConversationId(proto.getConversationId());
        suggestion.setActionType(proto.getActionType());
        suggestion.setTargetType(proto.getTargetType());
        suggestion.setTargetId(proto.getTargetId());
        suggestion.setParams(proto.getParams());
        suggestion.setReason(proto.getReason());
        suggestion.setPriority(proto.getPriority().isBlank() ? "NORMAL" : proto.getPriority());
        suggestionRepository.save(suggestion);
        log.info("async suggestion persisted: id={} action={} target={}/{} conversation={}",
                suggestion.getId(), suggestion.getActionType(), suggestion.getTargetType(),
                suggestion.getTargetId(), suggestion.getConversationId());
        return suggestion;
    }

    @Transactional
    public AgentSuggestion reject(Long id, Long confirmedBy) {
        AgentSuggestion suggestion = suggestionRepository.findById(id)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("建议不存在: " + id));
        if (!"PENDING".equals(suggestion.getStatus())) {
            throw new IllegalArgumentException("仅 PENDING 状态的建议可忽略，当前: " + suggestion.getStatus());
        }
        suggestion.setStatus("REJECTED");
        suggestion.setConfirmedBy(confirmedBy);
        suggestion.setConfirmedAt(OffsetDateTime.now());
        suggestionRepository.save(suggestion);
        log.info("suggestion rejected: id={}", id);
        return suggestion;
    }

    /** 从来源任务找到 worker（任务 workerId → 注册表）。 */
    private WorkerRegistry.WorkerEntry resolveWorker(AgentSuggestion suggestion) {
        if (suggestion.getTaskId() == null) {
            return null;
        }
        return taskRepository.findByTaskId(suggestion.getTaskId())
                .map(AgentTask::getWorkerId)
                .flatMap(workerRegistry::get)
                .orElse(null);
    }

    /**
     * 定时过期扫描：APPROVED 且 grantKey 已不在 Redis（TTL 到期未执行 / 已消费但执行任务未回写）且
     * 关联 execute_suggestion 任务不在执行中 → 置 EXPIRED（设计状态流转：APPROVED ──key 过期──► EXPIRED）。
     */
    @Scheduled(fixedDelay = 60_000)
    @Transactional
    public void expireScan() {
        for (AgentSuggestion s : suggestionRepository.findByStatus("APPROVED")) {
            if (s.getGrantKey() == null || grantService.exists(s.getGrantKey())) {
                continue; // key 仍有效（未过期）
            }
            if (executeTaskRunning(s)) {
                continue; // execute 任务仍在执行，等它回写（避免误置）
            }
            s.setStatus("EXPIRED");
            suggestionRepository.save(s);
            log.info("suggestion expired: id={} key={}", s.getId(), s.getGrantKey());
        }
    }

    /** 该建议的 execute_suggestion 任务是否还在执行（RUNNING/DISPATCHED）。 */
    private boolean executeTaskRunning(AgentSuggestion s) {
        String fragment = "\"suggestionId\":" + s.getId();
        return !taskRepository.findByTaskTypeAndQueryContainingAndStatusIn(
                "execute_suggestion", fragment,
                List.of("DISPATCHED", "RUNNING")).isEmpty();
    }
}
