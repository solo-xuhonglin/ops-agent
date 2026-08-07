package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.DatasetDto;
import com.opsagent.admin.service.DatasetService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/datasets")
@RequiredArgsConstructor
public class DatasetController {

    private final DatasetService datasetService;

    @GetMapping
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(datasetService.list(pageable));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(datasetService.get(id));
    }

    @PostMapping
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> create(@Valid @RequestBody DatasetDto.CreateRequest req) {
        return ApiResponse.ok(datasetService.create(req));
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> update(@PathVariable Long id, @Valid @RequestBody DatasetDto.UpdateRequest req) {
        return ApiResponse.ok(datasetService.update(id, req));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        datasetService.delete(id);
        return ApiResponse.ok();
    }
}
