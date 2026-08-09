package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 会话消息（多轮对话的持久化消息流）。
 * role: user / assistant / system；status: streaming / completed / failed。
 * task_id 关联该轮内部任务（AgentTask），reasoning 单独存推理链全文。
 */
@Entity
@Table(name = "conversation_messages")
@Getter
@Setter
@NoArgsConstructor
public class ConversationMessage {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "message_id", length = 64, nullable = false, unique = true)
    private String messageId;

    @Column(name = "conversation_id", length = 64, nullable = false)
    private String conversationId;

    @Column(length = 16, nullable = false)
    private String role;

    @Column(columnDefinition = "TEXT")
    private String content;

    /** LLM 推理链全文（assistant 消息可折叠展示） */
    @Column(columnDefinition = "TEXT")
    private String reasoning;

    /** streaming / completed / failed */
    @Column(length = 16, nullable = false)
    private String status;

    /** 该轮内部任务（assistant 消息关联） */
    @Column(name = "task_id", length = 64)
    private String taskId;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}
