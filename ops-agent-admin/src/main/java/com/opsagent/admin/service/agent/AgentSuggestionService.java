package com.opsagent.admin.service.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 处置建议闭环：suggestion 业务行（PENDING 创建、EXECUTED/FAILED 结果）由 worker 直写；
 * admin 只写审批动作：approve → APPROVED + grantKey(Redis) + 派发 execute 任务；reject → REJECTED；
 * expireScan → EXPIRED（grantKey 过期未执行）。
 * 审批/执行动作同步落 APPROVAL 消息（AgentConversationService.saveApprovalDecision），
 * 会话历史按时间序能看见完整审批闭环（pending → approved/rejected → executed/failed/expired）。
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
    private final AgentConversationService conversationService;
    private final ObjectMapper objectMapper;

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

        // 历史落库：APPROVAL 消息（pending → approved 状态变更，调用方按需再刷新执行结果）
        recordApproval(suggestion, "APPROVED");

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

        // 历史落库：APPROVAL 消息 REJECTED
        recordApproval(suggestion, "REJECTED");

        // 派发反馈任务：让模型基于历史（含 [审批] 已忽略 折叠行）输出一段反馈，
        // 而不是"点了忽略模型毫无反应"（feedback 轮不挂工具，只确认+说明下一步）
        try {
            String conversationId = suggestion.getConversationId();
            String history = (conversationId == null || conversationId.isBlank())
                    ? "" : conversationService.buildHistory(conversationId);
            taskService.dispatchFeedback(conversationId, suggestionId,
                    suggestion.getActionType(), suggestion.getTargetType(),
                    suggestion.getTargetId(), confirmedBy, history);
        } catch (Exception e) {
            log.warn("feedback dispatch after reject failed (ignored): sug={}, err={}",
                    suggestionId, e.getMessage());
        }

        log.info("suggestion rejected: id={}", suggestionId);
        return suggestion;
    }

    /**
     * execute 任务完成后刷新 APPROVAL 消息：按 taskId 反查 suggestion，更新决策行。
     * 由 AgentGrpcService 在收 TaskResult 时调用（execute 任务的结论落库后再次更新历史）。
     * 若 suggestion 已是终态（REJECTED/EXPIRED）则跳过，避免被异步执行结果反向覆盖用户拒绝的决策。
     */
    @Transactional
    public void refreshApprovalAfterExecuteTask(String taskId) {
        if (taskId == null || taskId.isBlank()) return;
        try {
            java.util.Optional<AgentTask> taskOpt = taskRepository.findByTaskId(taskId);
            if (taskOpt.isEmpty()) return;
            String suggestionId = taskOpt.get().getSuggestionId();
            if (suggestionId == null || suggestionId.isBlank()) return;
            java.util.Optional<AgentSuggestion> sugOpt =
                    suggestionRepository.findBySuggestionId(suggestionId);
            if (sugOpt.isEmpty()) return;
            AgentSuggestion s = sugOpt.get();
            // 不允许反向覆盖用户主动拒绝/已过期的状态
            if ("REJECTED".equals(s.getStatus()) || "EXPIRED".equals(s.getStatus())) {
                return;
            }
            recordApproval(s, s.getStatus());
        } catch (Exception e) {
            log.warn("refresh approval after execute failed (ignored): task={}, err={}",
                    taskId, e.getMessage());
        }
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
                recordApproval(s, "EXPIRED");
                log.info("suggestion expired: id={} key={}", s.getSuggestionId(), s.getGrantKey());
            }
        }
    }

    /**
     * 写一条 APPROVAL 消息（或 upsert 同 suggestionId 的现有行）。失败静默（主流程不能因为审计失败而被阻断）。
     * payload 含审批完整快照：action/target/params/reason/priority/decision/confirmedBy/executedAt/result。
     */
    private void recordApproval(AgentSuggestion s, String decision) {
        try {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("suggestionId", s.getSuggestionId());
            payload.put("planId", s.getPlanId());
            payload.put("stepNo", s.getStepNo());
            payload.put("actionType", s.getActionType());
            payload.put("targetType", s.getTargetType());
            payload.put("targetId", s.getTargetId());
            payload.put("params", s.getParams());
            payload.put("reason", s.getReason());
            payload.put("priority", s.getPriority());
            payload.put("decision", decision);
            payload.put("confirmedBy", s.getConfirmedBy());
            payload.put("confirmedAt", s.getConfirmedAt() == null ? null : s.getConfirmedAt().toString());
            payload.put("executedAt", s.getExecutedAt() == null ? null : s.getExecutedAt().toString());
            payload.put("result", s.getResult());
            payload.put("retryOf", s.getRetryOf());
            String payloadJson = objectMapper.writeValueAsString(payload);
            String taskId = s.getSuggestionId(); // APPROVAL 用 suggestionId 锚定（行粒度，而非一次任务）
            conversationService.saveApprovalDecision(
                    s.getConversationId(), taskId, s.getSuggestionId(), decision, payloadJson);
        } catch (Exception e) {
            log.warn("record approval failed (ignored): sug={}, err={}",
                    s.getSuggestionId(), e.getMessage());
        }
    }

    /** 该建议的 execute 任务是否还在执行（DISPATCHED/RUNNING）。 */
    private boolean executeTaskRunning(AgentSuggestion s) {
        return !taskRepository.findBySuggestionIdAndStatusIn(
                s.getSuggestionId(), List.of("DISPATCHED", "RUNNING")).isEmpty();
    }
}
