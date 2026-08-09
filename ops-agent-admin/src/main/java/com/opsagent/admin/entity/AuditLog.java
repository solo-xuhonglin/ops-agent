package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * 审计日志：仅系统写入（人类写操作经 AuditInterceptor、agent 写操作经 GrantCheckAspect），
 * 不暴露写接口。记录写操作的执行人、是否 agent、审批人、参数。
 */
@Entity
@Table(name = "audit_logs")
@Getter
@Setter
@NoArgsConstructor
public class AuditLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 写操作码：dataset:create / serving:deploy … */
    @Column(length = 64, nullable = false)
    private String action;

    /** USER / AGENT（即"是否 agent 执行"） */
    @Column(name = "actor_type", length = 16, nullable = false)
    private String actorType;

    /** 执行人：人类用户名 或 Agent */
    @Column(name = "actor_name", length = 128)
    private String actorName;

    /** agent 写操作的审批人（人类） */
    @Column(name = "approver_name", length = 128)
    private String approverName;

    @Column(name = "target_type", length = 64)
    private String targetType;

    @Column(name = "target_id")
    private Long targetId;

    /** 参数（JSON 字符串，已脱敏） */
    @Column(columnDefinition = "jsonb")
    private String params;

    @Column(length = 64)
    private String ip;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}
