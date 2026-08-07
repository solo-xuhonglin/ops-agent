package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.config.JwtProperties;
import com.opsagent.admin.dto.AuthDto;
import com.opsagent.admin.entity.Permission;
import com.opsagent.admin.entity.Role;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import com.opsagent.admin.security.JwtUtil;
import com.opsagent.admin.security.UserDetailsServiceImpl;
import lombok.RequiredArgsConstructor;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
@RequiredArgsConstructor
public class AuthService {

    private final AuthenticationManager authenticationManager;
    private final JwtUtil jwtUtil;
    private final UserRepository userRepository;
    private final UserDetailsServiceImpl userDetailsService;
    private final PasswordEncoder passwordEncoder;
    private final CurrentUser currentUser;

    public AuthDto.LoginResponse login(AuthDto.LoginRequest req) {
        authenticationManager.authenticate(
                new UsernamePasswordAuthenticationToken(req.username(), req.password()));
        User user = userRepository.findByUsername(req.username())
                .orElseThrow(() -> new ResourceNotFoundException("用户不存在"));
        UserDetails details = userDetailsService.loadUserByUsername(req.username());
        String token = jwtUtil.generateToken(user.getUsername(),
                details.getAuthorities().stream().map(a -> a.getAuthority()).toList());
        String refresh = jwtUtil.generateRefreshToken(user.getUsername());
        return new AuthDto.LoginResponse(
                token, refresh, user.getId(), user.getUsername(),
                user.getDisplayName(), getRoleNames(user), getPermissionCodes(user));
    }

    public AuthDto.LoginResponse refresh(AuthDto.RefreshRequest req) {
        if (!jwtUtil.isRefresh(req.refreshToken()) || jwtUtil.isExpired(req.refreshToken())) {
            throw new IllegalArgumentException("refresh token 无效或已过期");
        }
        String username = jwtUtil.getUsername(req.refreshToken());
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new ResourceNotFoundException("用户不存在"));
        UserDetails details = userDetailsService.loadUserByUsername(username);
        String token = jwtUtil.generateToken(username,
                details.getAuthorities().stream().map(a -> a.getAuthority()).toList());
        String refresh = jwtUtil.generateRefreshToken(username);
        return new AuthDto.LoginResponse(
                token, refresh, user.getId(), user.getUsername(),
                user.getDisplayName(), getRoleNames(user), getPermissionCodes(user));
    }

    public AuthDto.LoginResponse me() {
        String username = currentUser.username();
        if (username == null) throw new IllegalArgumentException("未认证");
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new ResourceNotFoundException("用户不存在"));
        UserDetails details = userDetailsService.loadUserByUsername(username);
        return new AuthDto.LoginResponse(
                null, null, user.getId(), user.getUsername(),
                user.getDisplayName(), getRoleNames(user), getPermissionCodes(user));
    }

    private List<String> getRoleNames(User user) {
        return user.getRoles().stream().map(Role::getName).toList();
    }

    private List<String> getPermissionCodes(User user) {
        return user.getRoles().stream()
                .flatMap(r -> r.getPermissions().stream())
                .map(Permission::getCode)
                .distinct().toList();
    }
}
