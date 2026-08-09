package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * 会话与业务对象关联（agent 写操作 → 创建的对象 ↔ 原会话）。
 * 由 ConversationLinkAspect 切面在 agent 写接口成功后自动记录，业务代码无需感知 conversation_id；
 * 用于异步 followup（如训练完成 → 把部署建议推回发起训练的原会话）。
 */
@Entity
@Table(name = "conversation_links")
@Getter
@Setter
@NoArgsConstructor
public class ConversationLink {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 发起该写操作的会话 ID（从 X-Agent-Task → AgentTask.conversationId 反查） */
    @Column(name = "conversation_id", length = 64, nullable = false)
    private String conversationId;

    /** 执行该写操作的 agent 任务 ID（execute_suggestion 任务） */
    @Column(name = "task_id", length = 64)
    private String taskId;

    /** 写操作类型（training_create / serving_deploy / ...） */
    @Column(name = "action_type", length = 32)
    private String actionType;

    /** 创建出的对象类型（training_job / serving_endpoint / ...） */
    @Column(name = "object_type", length = 32)
    private String objectType;

    /** 创建出的对象 ID（训练 job ID / serving endpoint ID） */
    @Column(name = "object_id")
    private Long objectId;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}