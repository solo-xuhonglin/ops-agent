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
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 训练完成 → 自动 followup：独立定时扫描（不挂在业务 poller 里，业务层零耦合）。
 * 从 conversation_links（追踪层关系表）找到未 followup 的训练对象 → 查业务状态（training_jobs 已 SUCCEEDED）
 * → 把部署评估推回原会话（agent 收到 training_completed_followup 任务后推 serving_deploy 建议供审批）。
 * 多步审批连接：用户问「训练并部署」→ 训练审批 → 训练完成 → 自动推部署审批。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class TrainingFollowupService {

    /** 轮询间隔：训练完成到触发部署建议的延迟上限 */
    private static final long SCAN_DELAY_MS = 30_000;

    private final TrainingJobRepository trainingJobRepository;
    private final ModelVersionRepository modelVersionRepository;
    private final ConversationRepository conversationRepository;
    private final ConversationLinkRepository linkRepository;
    private final AgentConversationService conversationService;

    /** 定时扫描：conversation_links 中未 followup 的训练对象，训练已 SUCCEEDED 则派发部署评估。 */
    @Scheduled(fixedDelay = SCAN_DELAY_MS)
    @Transactional
    public void scanFollowups() {
        for (ConversationLink link : linkRepository.findByObjectTypeAndFollowupDispatchedFalse("training_job")) {
            try {
                dispatchFollowup(link);
            } catch (Exception e) {
                log.warn("training followup scan error: link={} err={}", link.getId(), e.getMessage());
            }
        }
    }

    /** 单条 followup（幂等：link.followupDispatched 标志防重；训练未完成则跳过等下轮）。 */
    private void dispatchFollowup(ConversationLink link) {
        Long jobId = link.getObjectId();
        TrainingJob job = trainingJobRepository.findById(jobId).orElse(null);
        if (job == null || !"SUCCEEDED".equals(job.getStatus())) {
            return; // 训练还在跑/不存在：等下轮扫描
        }
        Conversation conversation = conversationRepository
                .findByConversationId(link.getConversationId()).orElse(null);
        if (conversation == null) {
            log.info("training followup skipped: conversation gone, jobId={}", jobId);
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
                jobId, modelVersionId, job.getDatasetId(),
                (metrics == null || metrics.isBlank()) ? "(无 metrics)" : metrics,
                modelVersionId);

        conversationService.send(link.getConversationId(), conversation.getUserId(),
                query, "training_completed_followup", "model_version", modelVersionId);
        link.setFollowupDispatched(true);
        linkRepository.save(link);
        log.info("training followup dispatched: jobId={}, modelVersionId={}, conversation={}",
                jobId, modelVersionId, link.getConversationId());
    }
}