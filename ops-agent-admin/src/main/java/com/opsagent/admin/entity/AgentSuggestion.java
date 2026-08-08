package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 处置建议（写操作须人工确认）：
 * PENDING → APPROVED(签发 grantKey 推给 agent) → EXECUTING → EXECUTED / FAILED
 * PENDING → REJECTED；APPROVED 但 key 超时未执行 → EXPIRED。
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

    @Column(name = "task_id", length = 64)
    private String taskId;

    /** 对应写工具名（training_delete / serving_undeploy / ...） */
    @Column(name = "action_type", length = 32, nullable = false)
    private String actionType;

    @Column(name = "target_type", length = 32, nullable = false)
    private String targetType;

    @Column(name = "target_id", nullable = false)
    private Long targetId;

    /** 业务参数（JSON 字符串，LLM 填写） */
    @Column(columnDefinition = "TEXT")
    private String params;

    /** 建议理由 */
    @Column(columnDefinition = "TEXT")
    private String reason;

    /** HIGH / NORMAL / LOW */
    @Column(length = 8)
    private String priority = "NORMAL";

    /** PENDING / APPROVED / REJECTED / EXECUTING / EXECUTED / FAILED / EXPIRED */
    @Column(length = 16, nullable = false)
    private String status = "PENDING";

    /** 确认后签发的 grantKey（审计留痕；Redis 是消费权威） */
    @Column(name = "grant_key", length = 64)
    private String grantKey;

    @Column(name = "confirmed_by")
    private Long confirmedBy;

    @Column(name = "confirmed_at")
    private OffsetDateTime confirmedAt;

    @Column(name = "executed_at")
    private OffsetDateTime executedAt;

    /** 执行回执（agent 报告） */
    @Column(columnDefinition = "TEXT")
    private String result;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
        if (status == null) {
            status = "PENDING";
        }
        if (priority == null) {
            priority = "NORMAL";
        }
    }
}
