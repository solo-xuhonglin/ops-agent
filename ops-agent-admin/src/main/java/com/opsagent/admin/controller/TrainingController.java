package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.TrainingRequest;
import com.opsagent.admin.entity.TrainingJob;
import com.opsagent.admin.service.TrainingJobService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/training/jobs")
@RequiredArgsConstructor
public class TrainingController {

    private final TrainingJobService trainingJobService;
    private final com.opsagent.admin.repository.AgentTaskRepository agentTaskRepository;

    @GetMapping
    @PreAuthorize("hasAuthority('training:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size,
                               @RequestParam(required = false) String status,
                               @RequestParam(required = false) Long datasetId) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(trainingJobService.list(pageable, status, datasetId));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAuthority('training:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(trainingJobService.get(id));
    }

    @PostMapping
    @PreAuthorize("hasAuthority('training:write')")
    @com.opsagent.admin.service.agent.RequireGrant(action = "training_create", targetType = "training_job", targetParam = "datasetId")
    public ApiResponse<?> create(@Valid @RequestBody TrainingRequest req,
                                 @RequestHeader(value = "X-Agent-Task", required = false) String agentTaskId) {
        // agent 调用时 X-Agent-Task = execute_suggestion 任务 ID → 反查其 conversationId 写入 job，
        // 供训练完成 → 自动 followup 把部署建议推回原会话（grantKey 一次性、任务关联持久，走 task 反查最直接）
        String conversationId = null;
        if (agentTaskId != null && !agentTaskId.isBlank()) {
            conversationId = agentTaskRepository.findByTaskId(agentTaskId)
                    .map(com.opsagent.admin.entity.AgentTask::getConversationId).orElse(null);
        }
        return ApiResponse.ok(trainingJobService.trigger(req, conversationId));
    }

    @GetMapping("/{id}/logs")
    @PreAuthorize("hasAuthority('training:read')")
    public ApiResponse<?> logs(@PathVariable Long id,
                              @RequestParam(defaultValue = "30") int expiryMinutes) {
        String url = trainingJobService.logsUrl(id, expiryMinutes);
        return ApiResponse.ok(Map.of("url", url));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('training:write')")
    @com.opsagent.admin.service.agent.RequireGrant(action = "training_delete", targetType = "training_job", targetParam = "id")
    public ApiResponse<?> delete(@PathVariable Long id) {
        trainingJobService.delete(id);
        return ApiResponse.ok();
    }
}
