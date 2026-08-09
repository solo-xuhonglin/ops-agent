package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * 任务计划（Plan）持久化：agent 第一次对话建立、每步执行更新（admin 仅落库，不做流程）。
 * steps 以 JSON 数组存储（[{action_type,target_type,target_id,params,reason,priority,status,object_type,object_id}]）。
 */
@Entity
@Table(name = "task_plans")
@Getter
@Setter
@NoArgsConstructor
public class TaskPlan {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "conversation_id", length = 64, nullable = false, unique = true)
    private String conversationId;

    @Column(name = "plan_id", length = 64)
    private String planId;

    /** 计划摘要（用户可见，如"训练并部署 LSTM 模型"） */
    @Column(columnDefinition = "TEXT")
    private String summary;

    /** PLANNED / RUNNING / DONE / FAILED */
    @Column(length = 16)
    private String status = "PLANNED";

    /** 步骤 JSON 数组（agent 上报，admin 原样存） */
    @Column(name = "steps_json", columnDefinition = "TEXT")
    private String stepsJson;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @Column(name = "updated_at")
    private OffsetDateTime updatedAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
        updatedAt = OffsetDateTime.now();
    }

    @PreUpdate
    void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }
}