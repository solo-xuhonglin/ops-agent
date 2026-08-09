package com.opsagent.admin.service.agent;

import com.opsagent.admin.entity.Conversation;
import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.entity.TrainingJob;
import com.opsagent.admin.repository.ConversationRepository;
import com.opsagent.admin.repository.ModelVersionRepository;
import com.opsagent.admin.repository.TrainingJobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 训练完成 → 自动 followup：把部署评估推回发起训练的原会话（training_jobs.conversation_id 直连），
 * agent 收到 training_completed_followup 任务后推 serving_deploy 建议供人工审批。
 * 多步审批连接（用户问「训练并部署」→ 训练审批 → 训练完成 → 自动推部署审批）。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class TrainingFollowupService {

    private final TrainingJobRepository trainingJobRepository;
    private final ModelVersionRepository modelVersionRepository;
    private final ConversationRepository conversationRepository;
    private final AgentConversationService conversationService;

    /**
     * 训练 SUCCEEDED 时由 TrainingJobPoller.finalizeSucceeded 调用；幂等（followupDispatched 标志防重）。
     * 会话归属用户 ID 从 conversation 表取（该会话的发起人），无会话（用户手动创建的训练）跳过。
     */
    @Transactional
    public void dispatchFollowup(TrainingJob job) {
        if (job == null || job.getConversationId() == null || job.getConversationId().isBlank()) {
            return;
        }
        if (job.isFollowupDispatched()) {
            log.debug("training followup already dispatched: jobId={}", job.getId());
            return;
        }
        Conversation conversation = conversationRepository
                .findByConversationId(job.getConversationId()).orElse(null);
        if (conversation == null) {
            log.info("training followup skipped: conversation gone, jobId={}", job.getId());
            return;
        }

        Long modelVersionId = job.getModelVersionId();
        String metrics = modelVersionRepository.findById(modelVersionId)
                .map(ModelVersion::getMetrics).orElse("");
        String query = String.format(
                "[系统通知] 训练任务已完成。\n" +
                "  - trainingJobId: %d\n" +
                "  - modelVersionId: %d\n" +
                "  - datasetId: %d\n" +
                "  - metrics: %s\n" +
                "请评估该模型是否需要部署到推理服务（serving_deploy）。如需部署，请在 suggestions JSON 块中给出"
                + " action_type=serving_deploy、target_type=model_version、target_id=%d 的部署建议。",
                job.getId(), modelVersionId, job.getDatasetId(),
                (metrics == null || metrics.isBlank()) ? "(无 metrics)" : metrics,
                modelVersionId);

        conversationService.send(job.getConversationId(), conversation.getUserId(),
                query, "training_completed_followup", "model_version", modelVersionId);
        job.setFollowupDispatched(true);
        trainingJobRepository.save(job);
        log.info("training followup dispatched: jobId={}, modelVersionId={}, conversation={}",
                job.getId(), modelVersionId, job.getConversationId());
    }
}