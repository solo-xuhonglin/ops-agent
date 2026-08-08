package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 任务事件流（进度上报，按 taskId + seq 有序）。
 */
@Entity
@Table(name = "agent_events")
@Getter
@Setter
@NoArgsConstructor
public class AgentEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", length = 64, nullable = false)
    private String taskId;

    @Column(nullable = false)
    private Integer seq;

    /** progress / tool_call / error */
    @Column(name = "event_type", length = 16, nullable = false)
    private String eventType;

    @Column(columnDefinition = "TEXT")
    private String content;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}
