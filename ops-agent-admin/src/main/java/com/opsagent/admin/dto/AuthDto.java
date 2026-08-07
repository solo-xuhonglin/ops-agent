package com.opsagent.admin.dto;

public class AuthDto {

    public record LoginRequest(String username, String password) {}

    public record RefreshRequest(String refreshToken) {}

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
