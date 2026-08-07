package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.dto.AuthDto;
import com.opsagent.admin.service.AuthService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    @PostMapping("/login")
    public ApiResponse<AuthDto.LoginResponse> login(@Valid @RequestBody AuthDto.LoginRequest req) {
        return ApiResponse.ok(authService.login(req));
    }

    @PostMapping("/refresh")
    public ApiResponse<AuthDto.LoginResponse> refresh(@Valid @RequestBody AuthDto.RefreshRequest req) {
        return ApiResponse.ok(authService.refresh(req));
    }

    @GetMapping("/me")
    public ApiResponse<AuthDto.LoginResponse> me() {
        return ApiResponse.ok(authService.me());
    }
}
