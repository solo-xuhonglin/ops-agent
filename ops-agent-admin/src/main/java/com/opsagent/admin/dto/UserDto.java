package com.opsagent.admin.dto;

import jakarta.validation.constraints.NotBlank;

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
            @NotBlank(message = "用户名不能为空") String username,
            @NotBlank(message = "密码不能为空") String password,
            String displayName,
            String email,
            List<Long> roleIds) {
    }

    public record UpdateRequest(
            String displayName,
            String email,
            @NotBlank(message = "状态不能为空") String status,
            List<Long> roleIds) {
    }

    public record ChangePasswordRequest(
            @NotBlank(message = "原密码不能为空") String oldPassword,
            @NotBlank(message = "新密码不能为空") String newPassword) {}
}
