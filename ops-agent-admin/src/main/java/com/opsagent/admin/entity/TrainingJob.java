package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

@Entity
@Table(name = "training_jobs")
@Getter
@Setter
@NoArgsConstructor
public class TrainingJob {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "model_version_id")
    private Long modelVersionId;

    @Column(name = "dataset_id")
    private Long datasetId;

    @Column(name = "container_id", length = 128)
    private String containerId;

    @Column(length = 16)
    private String status;

    @Column(name = "triggered_by")
    private Long triggeredBy;

    /** 触发本次训练的 agent 建议 ID（agent 调 training_create 时从 grantKey 解析写入；null 表示非 agent 触发） */
    @Column(name = "suggestion_id")
    private Long suggestionId;

    /** 训练完成后是否已派发 agent 新任务推部署建议（防重复触发） */
    @Column(name = "followup_dispatched")
    private boolean followupDispatched = false;

    @Column(name = "log_key", length = 512)
    private String logKey;

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
