package com.opsagent.admin.dto;

import jakarta.validation.constraints.NotBlank;

public class PermissionDto {

    public record Response(
            Long id,
            String code,
            String description) {
    }

    public record CreateRequest(
            @NotBlank(message = "权限码不能为空") String code,
            String description) {
    }
}
