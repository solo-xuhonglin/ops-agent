package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

@Entity
@Table(name = "serving_endpoints")
@Getter
@Setter
@NoArgsConstructor
public class ServingEndpoint {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "model_version_id")
    private Long modelVersionId;

    @Column(name = "container_id", length = 128)
    private String containerId;

    @Column(length = 128)
    private String host;

    @Column
    private Integer port;

    @Column(length = 255)
    private String url;

    @Column(length = 16)
    private String status;

    @Column(name = "deployed_by")
    private Long deployedBy;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @Column(name = "stopped_at")
    private OffsetDateTime stoppedAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}
