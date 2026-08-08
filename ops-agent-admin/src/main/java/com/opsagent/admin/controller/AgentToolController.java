package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.entity.AgentTool;
import com.opsagent.admin.repository.AgentToolRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * AI Agent 工具注册表管理面 API（人用，非 agent 能力接口）。
 * 能力=数据：查看工具清单（名称/描述/端点/权限/启停），切换 enabled ——
 * 修改即生效（agent 下次注册时按 enabled 动态下发 schema，无需改代码）。
 */
@RestController
@RequestMapping("/api/agent/tools")
@RequiredArgsConstructor
public class AgentToolController {

    private final AgentToolRepository toolRepository;

    @GetMapping
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> list() {
        return ApiResponse.ok(toolRepository.findAllByOrderByIdAsc());
    }

    /** 切换工具启停：disabled 的工具不再随注册下发（agent 无法调用）。 */
    @PutMapping("/{id}/enabled")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> setEnabled(@PathVariable Long id, @RequestBody Map<String, Object> body) {
        AgentTool tool = toolRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("工具不存在: " + id));
        Object enabled = body.get("enabled");
        if (!(enabled instanceof Boolean)) {
            throw new IllegalArgumentException("enabled 必须为布尔值");
        }
        tool.setEnabled((Boolean) enabled);
        toolRepository.save(tool);
        return ApiResponse.ok(tool);
    }
}
