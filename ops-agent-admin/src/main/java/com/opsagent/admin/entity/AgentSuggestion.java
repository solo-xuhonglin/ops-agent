package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 写操作建议（v3 重构）：plan 的步骤 + 审批对象。
 * 业务行（PENDING 创建、EXECUTED/FAILED 执行结果）由 worker 直写；
 * admin 只写审批动作：approve→APPROVED+grantKey、reject→REJECTED、expireScan→EXPIRED。
 * 状态机：PENDING → APPROVED → EXECUTING → EXECUTED / FAILED
 *              → REJECTED / EXPIRED / CANCELLED
 */
@Entity
@Table(name = "agent_suggestions")
@Getter
@Setter
@NoArgsConstructor
public class AgentSuggestion {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** v3 唯一业务标识（UUID，worker 生成；approve/执行回写用它） */
    @Column(name = "suggestion_id", length = 64, nullable = false, unique = true)
    private String suggestionId;

    /** 所属 plan（多步规划；null=非规划的单条建议） */
    @Column(name = "plan_id", length = 64)
    private String planId;

    /** plan 内步骤顺序（1..N） */
    @Column(name = "step_no")
    private Integer stepNo;

    /** 来源任务（chat 轮 / 触发决策的 execute 轮） */
    @Column(name = "source_task_id", length = 64)
    private String sourceTaskId;

    /** 所属会话（agent 在对话中提出；用于执行结果写回） */
    @Column(name = "conversation_id", length = 64, nullable = false)
    private String conversationId;

    /** 对应写工具名（training_create / serving_deploy / ...） */
    @Column(name = "action_type", length = 32, nullable = false)
    private String actionType;

    @Column(name = "target_type", length = 32)
    private String targetType;

    @Column(name = "target_id")
    private Long targetId;

    /** 业务参数（JSON 字符串） */
    @Column(columnDefinition = "TEXT")
    private String params;

    /** 建议理由 */
    @Column(columnDefinition = "TEXT")
    private String reason;

    /** HIGH / NORMAL / LOW */
    @Column(length = 8)
    private String priority = "NORMAL";

    /** PENDING / APPROVED / REJECTED / EXECUTING / EXECUTED / FAILED / EXPIRED / CANCELLED */
    @Column(length = 16, nullable = false)
    private String status = "PENDING";

    /** 确认后签发的 grantKey（审计留痕；Redis 是消费权威） */
    @Column(name = "grant_key", length = 128)
    private String grantKey;

    @Column(name = "confirmed_by")
    private Long confirmedBy;

    @Column(name = "confirmed_at")
    private OffsetDateTime confirmedAt;

    @Column(name = "executed_at")
    private OffsetDateTime executedAt;

    /** 执行回执（LLM 总结，worker 直写） */
    @Column(columnDefinition = "TEXT")
    private String result;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @Column(name = "updated_at")
    private OffsetDateTime updatedAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
        updatedAt = createdAt;
        if (status == null) {
            status = "PENDING";
        }
        if (priority == null) {
            priority = "NORMAL";
        }
    }

    @PreUpdate
    void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }
}
