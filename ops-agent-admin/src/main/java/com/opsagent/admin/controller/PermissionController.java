package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.PermissionDto;
import com.opsagent.admin.service.PermissionService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/permissions")
@RequiredArgsConstructor
public class PermissionController {

    private final PermissionService permissionService;

    @GetMapping
    @PreAuthorize("hasAuthority('permission:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "100") int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").ascending());
        return ApiResponse.ok(permissionService.list(pageable));
    }

    @PostMapping
    @PreAuthorize("hasAuthority('permission:write')")
    public ApiResponse<?> create(@Valid @RequestBody PermissionDto.CreateRequest req) {
        return ApiResponse.ok(permissionService.create(req));
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasAuthority('permission:write')")
    public ApiResponse<?> update(@PathVariable Long id, @Valid @RequestBody PermissionDto.CreateRequest req) {
        return ApiResponse.ok(permissionService.update(id, req));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('permission:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        permissionService.delete(id);
        return ApiResponse.ok();
    }
}
