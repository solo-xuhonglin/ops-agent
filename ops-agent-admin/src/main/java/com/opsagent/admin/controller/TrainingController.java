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
    private final com.opsagent.admin.service.agent.GrantService grantService;

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
                                 @RequestHeader(value = "X-Grant-Key", required = false) String grantKey) {
        // agent 调用时从 grantKey 解析 suggestionId 写入 job，供训练完成 → 自动 followup 反查 conversation
        Long suggestionId = grantService.getSuggestionId(grantKey).orElse(null);
        return ApiResponse.ok(trainingJobService.trigger(req, suggestionId));
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
