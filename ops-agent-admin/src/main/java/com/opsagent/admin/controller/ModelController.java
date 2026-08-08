package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.service.ModelVersionService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/models")
@RequiredArgsConstructor
public class ModelController {

    private final ModelVersionService modelVersionService;

    @GetMapping
    @PreAuthorize("hasAuthority('model:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(modelVersionService.list(pageable));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAuthority('model:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(modelVersionService.get(id));
    }

    @PostMapping
    @PreAuthorize("hasAuthority('model:write')")
    public ApiResponse<?> create(@RequestBody ModelVersion mv) {
        return ApiResponse.ok(modelVersionService.save(mv));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('model:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        modelVersionService.delete(id);
        return ApiResponse.ok();
    }

    @GetMapping("/{id}/download")
    @PreAuthorize("hasAuthority('model:read')")
    public ApiResponse<?> download(@PathVariable Long id,
                                  @RequestParam(defaultValue = "30") int expiryMinutes) {
        String url = modelVersionService.downloadUrl(id, expiryMinutes);
        return ApiResponse.ok(java.util.Map.of("url", url));
    }
}
