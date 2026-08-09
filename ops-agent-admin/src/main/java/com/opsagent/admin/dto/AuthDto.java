package com.opsagent.admin.dto;

import jakarta.validation.constraints.NotBlank;

public class AuthDto {

    public record LoginRequest(
            @NotBlank(message = "用户名不能为空") String username,
            @NotBlank(message = "密码不能为空") String password) {}

    public record RefreshRequest(
            @NotBlank(message = "refreshToken 不能为空") String refreshToken) {}

    public record LoginResponse(
            String token,
            String refreshToken,
            Long userId,
            String username,
            String displayName,
            java.util.List<String> roles,
            java.util.List<String> permissions) {
    }
}
