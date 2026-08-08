package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 任务记录（TaskDispatch 历史与状态）。
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

    @Column(name = "task_type", length = 32, nullable = false)
    private String taskType;

    @Column(name = "target_type", length = 32)
    private String targetType;

    @Column(name = "target_id")
    private Long targetId;

    @Column(columnDefinition = "TEXT")
    private String query;

    /** DISPATCHED / RUNNING / SUCCEEDED / FAILED / CANCELLED */
    @Column(length = 16, nullable = false)
    private String status;

    @Column(name = "dispatched_by")
    private Long dispatchedBy;

    @Column(name = "worker_id", length = 64)
    private String workerId;

    @Column(columnDefinition = "TEXT")
    private String conclusion;

    @Column(name = "started_at")
    private OffsetDateTime startedAt;

    @Column(name = "finished_at")
    private OffsetDateTime finishedAt;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}
