package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.TrainingJob;
import com.opsagent.admin.service.TrainingJobService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/training/jobs")
@RequiredArgsConstructor
public class TrainingController {

    private final TrainingJobService trainingJobService;

    @GetMapping
    @PreAuthorize("hasAuthority('training:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(trainingJobService.list(pageable));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAuthority('training:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(trainingJobService.get(id));
    }

    @PostMapping
    @PreAuthorize("hasAuthority('training:write')")
    public ApiResponse<?> create(@RequestBody TrainingJob job) {
        return ApiResponse.ok(trainingJobService.save(job));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('training:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        trainingJobService.delete(id);
        return ApiResponse.ok();
    }
}
