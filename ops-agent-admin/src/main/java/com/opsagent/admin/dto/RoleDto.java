package com.opsagent.admin.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.List;

public class RoleDto {

    public record Response(
            Long id,
            String name,
            String description,
            List<PermissionDto.Response> permissions) {
    }

    public record CreateRequest(
            @NotBlank(message = "角色名不能为空") String name,
            String description,
            List<Long> permissionIds) {
    }

    public record UpdateRequest(
            String description,
            List<Long> permissionIds) {
    }
}
