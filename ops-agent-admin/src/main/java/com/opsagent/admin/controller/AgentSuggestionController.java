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
 * AI Agent 处置建议管理面 API（人用）：列表 / 确认（签发 grantKey 推 agent）/ 忽略。
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

    @PostMapping("/{id}/approve")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> approve(@PathVariable Long id) {
        try {
            AgentSuggestion suggestion = suggestionService.approve(id, resolveUserId());
            return ApiResponse.ok(Map.of("id", suggestion.getId(), "status", suggestion.getStatus(),
                    "grantKey", suggestion.getGrantKey()));
        } catch (IllegalArgumentException e) {
            return ApiResponse.error("AGENT_OFFLINE_OR_STATE", e.getMessage());
        }
    }

    @PostMapping("/{id}/reject")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> reject(@PathVariable Long id) {
        try {
            AgentSuggestion suggestion = suggestionService.reject(id, resolveUserId());
            return ApiResponse.ok(Map.of("id", suggestion.getId(), "status", suggestion.getStatus()));
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
