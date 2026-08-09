package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 规划：一次规划 = 一行（意图），关联 N 条 agent_suggestions（步骤）与
 * N 条 agent_tasks（执行）。业务行由 worker 直写（asyncpg），admin 只读查询。
 * 状态：PLANNED → RUNNING → DONE / FAILED / CANCELLED。
 */
@Entity
@Table(name = "agent_plans")
@Getter
@Setter
@NoArgsConstructor
public class AgentPlan {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "plan_id", length = 64, nullable = false, unique = true)
    private String planId;

    @Column(name = "conversation_id", length = 64, nullable = false)
    private String conversationId;

    @Column(length = 255)
    private String summary;

    /** 步骤清单 JSON（worker 直写；模型掌舵每步状态）。 */
    @Column(columnDefinition = "text")
    private String steps;

    /** PLANNED / RUNNING / DONE / FAILED / CANCELLED */
    @Column(length = 16, nullable = false)
    private String status = "PLANNED";

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @Column(name = "updated_at")
    private OffsetDateTime updatedAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
        updatedAt = createdAt;
    }

    @PreUpdate
    void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }
}
