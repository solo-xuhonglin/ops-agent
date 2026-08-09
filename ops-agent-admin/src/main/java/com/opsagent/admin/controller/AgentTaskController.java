package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.AgentTaskRequest;
import com.opsagent.admin.entity.AgentEvent;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import com.opsagent.admin.service.agent.AgentTaskService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import java.util.List;
import java.util.Map;

/**
 * AI Agent 管理面 API（人用，非 agent 能力接口）。
 * 派发诊断/问询任务，查看任务状态与事件流。
 */
@RestController
@RequestMapping("/api/agent/tasks")
@RequiredArgsConstructor
public class AgentTaskController {

    private final AgentTaskService agentTaskService;
    private final UserRepository userRepository;
    private final CurrentUser currentUser;

    @PostMapping
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> dispatch(@Valid @RequestBody AgentTaskRequest req) {
        Long userId = resolveUserId();
        AgentTask task = agentTaskService.dispatch(req.getTaskType(), req.getTargetType(),
                req.getTargetId(), req.getQuery(), userId);
        return ApiResponse.ok(Map.of("taskId", task.getTaskId(), "status", task.getStatus()));
    }

    @GetMapping
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size,
                               @RequestParam(required = false) String status,
                               @RequestHeader(value = "X-Agent-Task", required = false) String agentTaskId) {
        if (agentTaskId != null && !agentTaskId.isBlank()) {
            // agent 追踪：X-Agent-Task 反查会话，只返回该会话内自己发起的任务（隔离其他用户/会话）
            String conversationId = agentTaskService.get(agentTaskId)
                    .map(AgentTask::getConversationId).orElse(null);
            if (conversationId != null) {
                return ApiResponse.ok(agentTaskService.listByConversation(conversationId, status, page, size));
            }
            return ApiResponse.ok(Page.empty());
        }
        return ApiResponse.ok(agentTaskService.list(page, size));
    }

    @GetMapping("/{taskId}")
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> get(@PathVariable String taskId) {
        AgentTask task = agentTaskService.get(taskId)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("任务不存在: " + taskId));
        List<AgentEvent> events = agentTaskService.events(taskId);
        return ApiResponse.ok(Map.of("task", task, "events", events));
    }

    private Long resolveUserId() {
        String username = currentUser.username();
        if (username == null) {
            return null;
        }
        return userRepository.findByUsername(username).map(u -> u.getId()).orElse(null);
    }
}
