package com.opsagent.admin.service.agent;

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
 * 处置建议闭环：suggestion 业务行（PENDING 创建、EXECUTED/FAILED 结果）由 worker 直写；
 * admin 只写审批动作：approve → APPROVED + grantKey(Redis) + 派发 execute 任务；reject → REJECTED；
 * expireScan → EXPIRED（grantKey 过期未执行）。
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
     * 确认建议：签发 grantKey（Redis）→ 条件更新 APPROVED → 派发 execute 任务（grant_key 随 TaskDispatch 下发）。
     * worker 离线时不签发，建议保持 PENDING 并抛 IllegalArgumentException（由 Controller 转提示）。
     */
    @Transactional
    public AgentSuggestion approve(String suggestionId, Long confirmedBy) {
        AgentSuggestion suggestion = suggestionRepository.findBySuggestionIdAndStatus(suggestionId, "PENDING")
                .orElseThrow(() -> new IllegalArgumentException("仅 PENDING 状态的建议可确认"));
        WorkerRegistry.WorkerEntry worker = workerRegistry.all().stream().findFirst().orElse(null);
        if (worker == null) {
            throw new IllegalArgumentException("agent 离线，无法下发授权，建议保持待确认");
        }

        // 授权 = 人工已确认该写操作：grant 只做一次性凭证校验（不比对 action/targetId）
        String grantKey = grantService.issue(suggestion.getActionType(), suggestion.getTargetType(),
                suggestion.getTargetId(), suggestion.getSuggestionId());
        suggestion.setStatus("APPROVED");
        suggestion.setGrantKey(grantKey);
        suggestion.setConfirmedBy(confirmedBy);
        suggestion.setConfirmedAt(OffsetDateTime.now());
        suggestionRepository.save(suggestion);

        // 派发 execute 任务：grant_key 随 TaskDispatch 下发；带 suggestion_id + conversation_id
        AgentTask task = taskService.dispatchExecute(
                suggestion.getConversationId(), suggestion.getSuggestionId(),
                suggestion.getActionType(), suggestion.getTargetType(), suggestion.getTargetId(),
                suggestion.getParams(), grantKey, confirmedBy);

        log.info("suggestion approved: id={} grantKey={} worker={}, execute task={} dispatched",
                suggestionId, grantKey, worker.getWorkerId(), task.getTaskId());
        return suggestion;
    }

    @Transactional
    public AgentSuggestion reject(String suggestionId, Long confirmedBy) {
        AgentSuggestion suggestion = suggestionRepository.findBySuggestionIdAndStatus(suggestionId, "PENDING")
                .orElseThrow(() -> new IllegalArgumentException("仅 PENDING 状态的建议可忽略"));
        suggestion.setStatus("REJECTED");
        suggestion.setConfirmedBy(confirmedBy);
        suggestion.setConfirmedAt(OffsetDateTime.now());
        suggestionRepository.save(suggestion);
        log.info("suggestion rejected: id={}", suggestionId);
        return suggestion;
    }

    /**
     * 定时过期扫描：APPROVED/EXECUTING 且 grantKey 已不在 Redis（TTL 到期未执行）且
     * 关联 execute 任务不在执行中 → 置 EXPIRED。
     */
    @Scheduled(fixedDelay = 60_000)
    @Transactional
    public void expireScan() {
        for (String status : List.of("APPROVED", "EXECUTING")) {
            for (AgentSuggestion s : suggestionRepository.findByStatus(status)) {
                if (s.getGrantKey() == null || grantService.exists(s.getGrantKey())) {
                    continue; // key 仍有效（未过期）
                }
                if (executeTaskRunning(s)) {
                    continue; // execute 任务仍在执行，等 worker 回写
                }
                s.setStatus("EXPIRED");
                suggestionRepository.save(s);
                log.info("suggestion expired: id={} key={}", s.getSuggestionId(), s.getGrantKey());
            }
        }
    }

    /** 该建议的 execute 任务是否还在执行（DISPATCHED/RUNNING）。 */
    private boolean executeTaskRunning(AgentSuggestion s) {
        return !taskRepository.findBySuggestionIdAndStatusIn(
                s.getSuggestionId(), List.of("DISPATCHED", "RUNNING")).isEmpty();
    }
}
