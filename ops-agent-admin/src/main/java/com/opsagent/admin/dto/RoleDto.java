package com.opsagent.admin.dto;

import java.util.List;

public class RoleDto {

    public record Response(
            Long id,
            String name,
            String description,
            List<PermissionDto.Response> permissions) {
    }

    public record CreateRequest(
            String name,
            String description,
            List<Long> permissionIds) {
    }

    public record UpdateRequest(
            String description,
            List<Long> permissionIds) {
    }
}
