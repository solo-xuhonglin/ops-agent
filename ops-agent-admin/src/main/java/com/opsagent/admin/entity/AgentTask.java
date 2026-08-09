package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 执行记录（v3 重构）：chat 对话轮 / execute 执行轮。
 * 业务行由 worker 直写（asyncpg）；admin 只读查询 + 供审批动作（approve 生成 execute 任务）与取消。
 * 关联：plan_id → agent_plans；suggestion_id → agent_suggestions。
 */
@Entity
@Table(name = "agent_tasks")
@Getter
@Setter
@NoArgsConstructor
public class AgentTask {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", length = 64, nullable = false, unique = true)
    private String taskId;

    /** chat（对话轮）| execute（执行已审批建议） */
    @Column(name = "task_type", length = 24, nullable = false)
    private String taskType;

    /** 所属 plan（execute 有；chat 可为空） */
    @Column(name = "plan_id", length = 64)
    private String planId;

    /** execute 对应建议（v3 为 UUID 字符串） */
    @Column(name = "suggestion_id", length = 64)
    private String suggestionId;

    @Column(name = "conversation_id", length = 64)
    private String conversationId;

    @Column(columnDefinition = "TEXT")
    private String query;

    /** DISPATCHED / RUNNING / SUCCEEDED / FAILED / CANCELLED */
    @Column(length = 16, nullable = false)
    private String status;

    @Column(name = "worker_id", length = 64)
    private String workerId;

    @Column(columnDefinition = "TEXT")
    private String conclusion;

    /** LLM 推理链全文（chat 轮） */
    @Column(columnDefinition = "TEXT")
    private String reasoning;

    @Column(name = "started_at")
    private OffsetDateTime startedAt;

    @Column(name = "finished_at")
    private OffsetDateTime finishedAt;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @Column(name = "updated_at")
    private OffsetDateTime updatedAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
        updatedAt = createdAt;
        if (status == null) {
            status = "DISPATCHED";
        }
    }

    @PreUpdate
    void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }
}
