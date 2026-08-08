package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.ToolSchema;
import com.opsagent.admin.repository.AgentToolRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 工具 schema 下发：从 agent_tools 表组装 OpenAI function calling 格式的 schema，
 * 随 RegisterAck 推给 agent（agent 零硬编码，工具增减只改库）。
 */
@Service
@RequiredArgsConstructor
public class ToolSchemaService {

    private final AgentToolRepository repository;

    /** 注册时下发启用的只读工具（写工具待 M3 grantKey 机制接入） */
    public List<ToolSchema> readToolSchemas() {
        return repository.findByEnabledTrueAndIsWriteFalseOrderByIdAsc().stream()
                .map(t -> ToolSchema.newBuilder()
                        .setName(t.getName())
                        .setDescription(t.getDescription())
                        .setParameters(t.getParamsSchema())
                        .setIsWrite(Boolean.TRUE.equals(t.getIsWrite()))
                        .setHttpMethod(t.getHttpMethod())
                        .setPathTemplate(t.getPathTemplate())
                        .build())
                .toList();
    }
}
