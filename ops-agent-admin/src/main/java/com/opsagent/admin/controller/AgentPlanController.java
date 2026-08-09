package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.AgentPlan;
import com.opsagent.admin.entity.AgentSuggestion;
import com.opsagent.admin.repository.AgentPlanRepository;
import com.opsagent.admin.repository.AgentSuggestionRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * AI Agent 规划管理面 API（人用，只读）：plan 卡片（plan + 步骤 suggestions 进度）。
 * plans/suggestions 业务行由 worker 直写；admin 仅查询。
 */
@RestController
@RequestMapping("/api/agent/plans")
@RequiredArgsConstructor
public class AgentPlanController {

    private final AgentPlanRepository planRepository;
    private final AgentSuggestionRepository suggestionRepository;

    /** 会话的规划列表（新→旧）。 */
    @GetMapping
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> list(@RequestParam String conversationId) {
        List<AgentPlan> plans = planRepository.findByConversationIdOrderByIdDesc(conversationId);
        return ApiResponse.ok(plans);
    }

    /** 单个 plan + 步骤（suggestions 按 step_no 升序，前端渲染进度）。 */
    @GetMapping("/{planId}")
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> get(@PathVariable String planId) {
        AgentPlan plan = planRepository.findByPlanId(planId)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("计划不存在: " + planId));
        List<AgentSuggestion> steps = suggestionRepository.findByPlanIdOrderByStepNoAsc(planId);
        return ApiResponse.ok(Map.of("plan", plan, "steps", steps));
    }
}
