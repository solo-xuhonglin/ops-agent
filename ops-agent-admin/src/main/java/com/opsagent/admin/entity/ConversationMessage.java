package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 会话消息流（多轮对话 + 工具调用/结果/审批事件）。
 * 历史渲染按时间序（id asc）拼接成统一时间线：
 * - kind=USER：用户消息（content）
 * - kind=ASSISTANT：助手答复（content + reasoning）
 * - kind=TOOL_CALL：模型发起的一次工具调用（tool_call_id + tool_name + tool_args）
 * - kind=TOOL_RESULT：上述调用的结果（tool_call_id 关联 + tool_name + tool_summary）
 * - kind=APPROVAL：建议审批动作（payload_json 含 suggestionId/actionType/decision/confirmedBy）
 *   同一建议可能存多条：PENDING 创建行 + 后续 decision/confirmation/event 增量更新行（最终状态以最新行为准）
 * 状态机（status）：streaming / completed / failed（仅对 ASSISTANT/USER 有意义，TOOL_* 固定 completed）。
 */
@Entity
@Table(name = "agent_conversation_messages")
@Getter
@Setter
@NoArgsConstructor
public class ConversationMessage {

    public static final String KIND_USER = "USER";
    public static final String KIND_ASSISTANT = "ASSISTANT";
    public static final String KIND_TOOL_CALL = "TOOL_CALL";
    public static final String KIND_TOOL_RESULT = "TOOL_RESULT";
    public static final String KIND_APPROVAL = "APPROVAL";

    public static final String STATUS_COMPLETED = "completed";
    public static final String STATUS_STREAMING = "streaming";
    public static final String STATUS_FAILED = "failed";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "message_id", length = 64, nullable = false, unique = true)
    private String messageId;

    @Column(name = "conversation_id", length = 64, nullable = false)
    private String conversationId;

    /** USER / ASSISTANT / TOOL_CALL / TOOL_RESULT / APPROVAL */
    @Column(length = 16, nullable = false)
    private String kind;

    /** 兼容历史 SQL 直接按 role 过滤的代码路径：新写入由 @PrePersist 从 kind 派生。 */
    @Column(length = 16)
    private String role;

    @Column(columnDefinition = "TEXT")
    private String content;

    /** LLM 推理链全文（仅 ASSISTANT 消息使用） */
    @Column(columnDefinition = "TEXT")
    private String reasoning;

    /** streaming / completed / failed */
    @Column(length = 16, nullable = false)
    private String status;

    /** 该轮内部任务（ASSISTANT 消息或该轮的 TOOL_CALL/TOOL_RESULT/APPROVAL 关联） */
    @Column(name = "task_id", length = 64)
    private String taskId;

    /** TOOL_CALL ↔ TOOL_RESULT 配对（同一 LLM 一次原生 tool_call 共享 call_id） */
    @Column(name = "tool_call_id", length = 64)
    private String toolCallId;

    /** TOOL_CALL/TOOL_RESULT 的工具名（如 dataset_list、approve_training_create） */
    @Column(name = "tool_name", length = 64)
    private String toolName;

    /** TOOL_CALL 的入参 JSON 字符串（前端 pretty 后展示） */
    @Column(name = "tool_args", columnDefinition = "TEXT")
    private String toolArgs;

    /** TOOL_RESULT 的截断结果摘要（≤500 字符，与 SSE 一致） */
    @Column(name = "tool_summary", columnDefinition = "TEXT")
    private String toolSummary;

    /** APPROVAL 的结构化数据：建议快照 + 审批结果（JSON 字符串） */
    @Column(name = "payload_json", columnDefinition = "TEXT")
    private String payloadJson;

    /** APPROVAL 审批结果（PENDING/APPROVED/REJECTED/EXECUTED/FAILED/EXPIRED），方便 SQL 筛选 */
    @Column(length = 16)
    private String decision;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
        if (status == null) status = STATUS_COMPLETED;
        if (kind == null) kind = KIND_ASSISTANT;
        // 镜像 role 列（兼容历史 SQL 直接按 role 过滤的代码路径 —— 新代码请用 kind）
        switch (kind) {
            case KIND_USER -> this.role = "user";
            case KIND_ASSISTANT -> this.role = "assistant";
            case KIND_TOOL_CALL, KIND_TOOL_RESULT -> this.role = "tool";
            case KIND_APPROVAL -> this.role = "approval";
            default -> this.role = "system";
        }
    }
}
