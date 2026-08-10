package com.opsagent.admin.service.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.entity.ConversationMessage;
import com.opsagent.admin.repository.ConversationMessageRepository;
import com.opsagent.admin.repository.ConversationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * buildHistory 集成测试：审批决策行（APPROVAL）直接进模型历史上下文——
 * 不再被过滤，content 复用 buildApprovalSummary 生成的最新决策摘要，加 [审批] 前缀，
 * role 映射为 assistant（worker 端只认 user/assistant）。
 * 目的：让模型在下一轮对话里能读到"该操作已授权/已拒绝/已执行/已失败/已过期"，
 * 修复"提交审批后模型失忆、重复申请或误判"。
 */
class AgentConversationServiceBuildHistoryTest {

    private ConversationMessageRepository messageRepository;
    private AgentConversationService service;

    @BeforeEach
    void setUp() {
        messageRepository = mock(ConversationMessageRepository.class);
        service = new AgentConversationService(
                mock(ConversationRepository.class),
                messageRepository,
                mock(AgentTaskService.class),
                mock(ConversationStreamManager.class),
                new ObjectMapper());
    }

    private ConversationMessage userMsg(String content) {
        ConversationMessage m = new ConversationMessage();
        m.setMessageId("user-1");
        m.setConversationId("c-1");
        m.setKind(ConversationMessage.KIND_USER);
        m.setStatus(ConversationMessage.STATUS_COMPLETED);
        m.setContent(content);
        return m;
    }

    private ConversationMessage assistantMsg(String content) {
        ConversationMessage m = new ConversationMessage();
        m.setMessageId("assist-1");
        m.setConversationId("c-1");
        m.setKind(ConversationMessage.KIND_ASSISTANT);
        m.setRole("assistant");
        m.setStatus(ConversationMessage.STATUS_COMPLETED);
        m.setContent(content);
        return m;
    }

    /** APPROVAL 行的 content 由 saveApprovalDecision 里 buildApprovalSummary 生成（如"已执行 · serving_deploy (model_version:15)"）。 */
    private ConversationMessage approvalMsg(String content, String decision) {
        ConversationMessage m = new ConversationMessage();
        m.setMessageId("approval-1");
        m.setConversationId("c-1");
        m.setKind(ConversationMessage.KIND_APPROVAL);
        m.setRole("approval");
        m.setStatus(ConversationMessage.STATUS_COMPLETED);
        m.setDecision(decision);
        m.setContent(content);
        return m;
    }

    @Test
    void buildHistoryIncludesApprovalDecisions() throws Exception {
        // 时间序：user → assistant → [审批]已执行
        when(messageRepository.findByConversationIdAndStatusInOrderByIdDesc(
                any(), anyList())).thenReturn(List.of(
                approvalMsg("已执行 · serving_deploy (model_version:15)", "EXECUTED"),
                assistantMsg("已提交部署建议，等待审批。"),
                userMsg("好的，请继续")));

        String historyJson = service.buildHistory("c-1");
        ObjectMapper om = new ObjectMapper();
        List<?> items = om.readValue(historyJson, List.class);

        assertThat(items).hasSize(3);
        java.util.Map<?, ?> approval = (java.util.Map<?, ?>) items.get(2);
        // role 映射为 assistant（worker 只认 user/assistant）
        assertThat(approval.get("role")).isEqualTo("assistant");
        // content = 原决策摘要 + [审批] 前缀
        assertThat(approval.get("content")).isEqualTo("[审批] 已执行 · serving_deploy (model_version:15)");
    }

    @Test
    void buildHistoryMapsApprovedAndRejectedRoles() throws Exception {
        // 已授权 / 已拒绝 同样进历史
        when(messageRepository.findByConversationIdAndStatusInOrderByIdDesc(
                any(), anyList())).thenReturn(List.of(
                approvalMsg("已拒绝 · training_create (dataset:13)", "REJECTED"),
                approvalMsg("已授权 · serving_deploy (model_version:15)", "APPROVED"),
                userMsg("继续推进")));

        String historyJson = service.buildHistory("c-1");
        ObjectMapper om = new ObjectMapper();
        List<?> items = om.readValue(historyJson, List.class);

        assertThat(items).hasSize(3);
        assertThat(((java.util.Map<?, ?>) items.get(1)).get("content"))
                .isEqualTo("[审批] 已授权 · serving_deploy (model_version:15)");
        assertThat(((java.util.Map<?, ?>) items.get(2)).get("content"))
                .isEqualTo("[审批] 已拒绝 · training_create (dataset:13)");
    }

    @Test
    void buildHistorySkipsApprovalWithoutContent() throws Exception {
        // content 为空 → 该行跳过，不影响其他消息
        ConversationMessage emptyApproval = approvalMsg(null, "APPROVED");
        when(messageRepository.findByConversationIdAndStatusInOrderByIdDesc(
                any(), anyList())).thenReturn(List.of(
                emptyApproval,
                userMsg("你好")));

        String historyJson = service.buildHistory("c-1");
        ObjectMapper om = new ObjectMapper();
        List<?> items = om.readValue(historyJson, List.class);
        assertThat(items).hasSize(1);
        assertThat(((java.util.Map<?, ?>) items.get(0)).get("role")).isEqualTo("user");
    }

    @Test
    void buildHistoryFiltersToolRowsAsBefore() throws Exception {
        // TOOL_CALL / TOOL_RESULT 仍不进历史（回归）
        ConversationMessage tool = new ConversationMessage();
        tool.setMessageId("tc-1");
        tool.setConversationId("c-1");
        tool.setKind(ConversationMessage.KIND_TOOL_CALL);
        tool.setRole("tool");
        tool.setStatus(ConversationMessage.STATUS_COMPLETED);
        tool.setContent("调用工具 dataset_get");
        when(messageRepository.findByConversationIdAndStatusInOrderByIdDesc(
                any(), anyList())).thenReturn(List.of(
                tool,
                userMsg("查询数据集")));

        String historyJson = service.buildHistory("c-1");
        ObjectMapper om = new ObjectMapper();
        List<?> items = om.readValue(historyJson, List.class);
        assertThat(items).hasSize(1);
        assertThat(((java.util.Map<?, ?>) items.get(0)).get("content")).isEqualTo("查询数据集");
    }
}
