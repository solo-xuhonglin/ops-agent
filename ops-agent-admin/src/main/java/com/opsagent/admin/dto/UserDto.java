package com.opsagent.admin.dto;

import java.util.List;

public class UserDto {

    public record Response(
            Long id,
            String username,
            String displayName,
            String email,
            String status,
            List<String> roles) {
    }

    public record CreateRequest(
            String username,
            String password,
            String displayName,
            String email,
            List<Long> roleIds) {
    }

    public record UpdateRequest(
            String displayName,
            String email,
            String status,
            List<Long> roleIds) {
    }

    public record ChangePasswordRequest(String oldPassword, String newPassword) {}
}
