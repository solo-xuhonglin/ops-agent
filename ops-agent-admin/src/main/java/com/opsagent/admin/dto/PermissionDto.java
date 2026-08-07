package com.opsagent.admin.dto;

public class PermissionDto {

    public record Response(
            Long id,
            String code,
            String description) {
    }

    public record CreateRequest(
            String code,
            String description) {
    }
}
