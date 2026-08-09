package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.AgentTaskRequest;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import com.opsagent.admin.service.agent.AgentTaskService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * AI Agent 管理面 API（人用）：派发对话/诊断任务、任务列表与详情、取消。
 * 任务行由 worker 直写；本类只读查询 + 取消转发（CancelTask）。
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
        AgentTask task = agentTaskService.dispatchChat(null, req.getQuery(), null,
                req.getTargetType(), req.getTargetId(), resolveUserId(),
                Boolean.TRUE.equals(req.getReasoning()));
        return ApiResponse.ok(Map.of("taskId", task.getTaskId(), "status", task.getStatus()));
    }

    @GetMapping
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        return ApiResponse.ok(agentTaskService.list(page, size));
    }

    @GetMapping("/{taskId}")
    @PreAuthorize("hasAuthority('agent:read')")
    public ApiResponse<?> get(@PathVariable String taskId) {
        AgentTask task = agentTaskService.get(taskId)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("任务不存在: " + taskId));
        return ApiResponse.ok(task);
    }

    /** 取消任务（前端「停止」）：发 CancelTask，worker 自治置 CANCELLED 并回写关联状态。 */
    @PostMapping("/{taskId}/cancel")
    @PreAuthorize("hasAuthority('agent:write')")
    public ApiResponse<?> cancel(@PathVariable String taskId) {
        agentTaskService.cancel(taskId, "cancelled by user");
        return ApiResponse.ok(Map.of("taskId", taskId, "status", "cancelling"));
    }

    private Long resolveUserId() {
        String username = currentUser.username();
        if (username == null) {
            return null;
        }
        return userRepository.findByUsername(username).map(u -> u.getId()).orElse(null);
    }
}
