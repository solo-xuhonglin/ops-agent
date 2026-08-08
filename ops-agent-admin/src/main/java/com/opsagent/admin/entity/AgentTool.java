package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.OffsetDateTime;

/**
 * AI Agent 工具注册表（能力=数据，admin 下发 schema，agent 零硬编码）。
 * 映射 admin 现有 REST 端点；写操作（is_write=true）需 grantKey 授权（M3 启用）。
 */
@Entity
@Table(name = "agent_tools")
@Getter
@Setter
@NoArgsConstructor
public class AgentTool {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(length = 64, nullable = false, unique = true)
    private String name;

    @Column(columnDefinition = "TEXT", nullable = false)
    private String description;

    @Column(name = "http_method", length = 8, nullable = false)
    private String httpMethod;

    @Column(name = "path_template", length = 255, nullable = false)
    private String pathTemplate;

    @Column(name = "auth_permission", length = 64)
    private String authPermission;

    @Column(name = "is_write", nullable = false)
    private Boolean isWrite = false;

    /** OpenAI JSON Schema 的 JSON 字符串（仅业务参数） */
    @Column(name = "params_schema", columnDefinition = "TEXT", nullable = false)
    private String paramsSchema;

    @Column(nullable = false)
    private Boolean enabled = true;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist
    void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}
