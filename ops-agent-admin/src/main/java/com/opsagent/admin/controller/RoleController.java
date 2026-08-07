package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.RoleDto;
import com.opsagent.admin.service.RoleService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/roles")
@RequiredArgsConstructor
public class RoleController {

    private final RoleService roleService;

    @GetMapping
    @PreAuthorize("hasAuthority('role:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "50") int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").ascending());
        return ApiResponse.ok(roleService.list(pageable));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAuthority('role:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(roleService.get(id));
    }

    @PostMapping
    @PreAuthorize("hasAuthority('role:write')")
    public ApiResponse<?> create(@Valid @RequestBody RoleDto.CreateRequest req) {
        return ApiResponse.ok(roleService.create(req));
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasAuthority('role:write')")
    public ApiResponse<?> update(@PathVariable Long id, @Valid @RequestBody RoleDto.UpdateRequest req) {
        return ApiResponse.ok(roleService.update(id, req));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('role:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        roleService.delete(id);
        return ApiResponse.ok();
    }
}
