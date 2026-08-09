package com.opsagent.admin.service.agent;

import com.opsagent.admin.entity.Conversation;
import com.opsagent.admin.entity.ConversationLink;
import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.entity.TrainingJob;
import com.opsagent.admin.repository.ConversationLinkRepository;
import com.opsagent.admin.repository.ConversationRepository;
import com.opsagent.admin.repository.ModelVersionRepository;
import com.opsagent.admin.repository.TrainingJobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 训练完成 → 自动 followup：从 conversation_links 关系表反查发起训练的原会话，
 * 把部署评估推回该会话（agent 收到 training_completed_followup 任务后推 serving_deploy 建议供审批）。
 * 多步审批连接（用户问「训练并部署」→ 训练审批 → 训练完成 → 自动推部署审批）。
 * 业务侧不持有 conversation_id，全靠切面记录的关系表反查。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class TrainingFollowupService {

    private final TrainingJobRepository trainingJobRepository;
    private final ModelVersionRepository modelVersionRepository;
    private final ConversationRepository conversationRepository;
    private final ConversationLinkRepository linkRepository;
    private final AgentConversationService conversationService;

    /**
     * 训练 SUCCEEDED 时由 TrainingJobPoller.finalizeSucceeded 调用；幂等（followupDispatched 标志防重）。
     * 无关联会话（用户手动创建的训练 / 关系表缺失）跳过。
     */
    @Transactional
    public void dispatchFollowup(TrainingJob job) {
        if (job == null || job.isFollowupDispatched()) {
            return;
        }
        ConversationLink link = linkRepository
                .findFirstByObjectTypeAndObjectId("training_job", job.getId()).orElse(null);
        if (link == null || link.getConversationId() == null || link.getConversationId().isBlank()) {
            log.info("training followup skipped: no conversation link for jobId={}", job.getId());
            return;
        }
        Conversation conversation = conversationRepository
                .findByConversationId(link.getConversationId()).orElse(null);
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

        conversationService.send(link.getConversationId(), conversation.getUserId(),
                query, "training_completed_followup", "model_version", modelVersionId);
        job.setFollowupDispatched(true);
        trainingJobRepository.save(job);
        log.info("training followup dispatched: jobId={}, modelVersionId={}, conversation={}",
                job.getId(), modelVersionId, link.getConversationId());
    }
}