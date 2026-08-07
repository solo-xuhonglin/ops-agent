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
