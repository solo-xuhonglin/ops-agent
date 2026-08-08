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
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;

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

        // 派发"执行建议"任务：grantKey 已在 agent 侧 GrantStore，LLM 按 query（含 target）调写工具（自动带 key），结果回传更新状态
        String query = "{\"suggestionId\":%d,\"action\":\"%s\",\"targetType\":\"%s\",\"targetId\":%d,\"params\":%s}"
                .formatted(suggestion.getId(), suggestion.getActionType(),
                        suggestion.getTargetType(), suggestion.getTargetId(),
                        suggestion.getParams() == null ? "{}" : suggestion.getParams());
        taskService.dispatch("execute_suggestion", suggestion.getTargetType(),
                suggestion.getTargetId(), query, confirmedBy);

        log.info("suggestion approved: id={} grantKey={} worker={}, execute task dispatched",
                id, grantKey, worker.getWorkerId());
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
}
