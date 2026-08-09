package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.AgentSuggestion;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import com.opsagent.admin.service.agent.AgentSuggestionService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * AI Agent 处置建议管理面 API（人用）：列表 / 确认（签发 grantKey 并派发 execute 任务）/ 忽略。
 * suggestion 业务行由 worker 直写；admin 只做审批动作。
 */
@RestController
@RequestMapping("/api/agent/suggestions")
@RequiredArgsConstructor
public class AgentSuggestionController {

    private final AgentSuggestionService suggestionService;
    private final UserRepository userRepository;
    private final CurrentUser currentUser;

    @GetMapping
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        Page<AgentSuggestion> result = suggestionService.list(page, size);
        return ApiResponse.ok(result);
    }

    @PostMapping("/{suggestionId}/approve")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> approve(@PathVariable String suggestionId) {
        try {
            AgentSuggestion suggestion = suggestionService.approve(suggestionId, resolveUserId());
            return ApiResponse.ok(Map.of("suggestionId", suggestion.getSuggestionId(),
                    "status", suggestion.getStatus(), "grantKey", suggestion.getGrantKey()));
        } catch (IllegalArgumentException e) {
            return ApiResponse.error("AGENT_OFFLINE_OR_STATE", e.getMessage());
        }
    }

    @PostMapping("/{suggestionId}/reject")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> reject(@PathVariable String suggestionId) {
        try {
            AgentSuggestion suggestion = suggestionService.reject(suggestionId, resolveUserId());
            return ApiResponse.ok(Map.of("suggestionId", suggestion.getSuggestionId(),
                    "status", suggestion.getStatus()));
        } catch (IllegalArgumentException e) {
            return ApiResponse.error("SUGGESTION_STATE", e.getMessage());
        }
    }

    private Long resolveUserId() {
        String username = currentUser.username();
        if (username == null) {
            return null;
        }
        return userRepository.findByUsername(username).map(u -> u.getId()).orElse(null);
    }
}
