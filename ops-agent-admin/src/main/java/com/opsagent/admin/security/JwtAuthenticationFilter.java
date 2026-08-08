package com.opsagent.admin.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.lang.NonNull;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

@Component
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtUtil jwtUtil;
    private final UserDetailsServiceImpl userDetailsService;

    @Override
    protected void doFilterInternal(@NonNull HttpServletRequest request,
                                    @NonNull HttpServletResponse response,
                                    @NonNull FilterChain filterChain) throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith("Bearer ")) {
            String token = header.substring(7);
            try {
                if (SecurityContextHolder.getContext().getAuthentication() == null) {
                    if (jwtUtil.isScoped(token)) {
                        // agent 任务级 token：权限直接取自 claims（裁剪的只读权限），不查用户库
                        var claims = jwtUtil.parse(token);
                        List<String> permissions = claims.get("permissions", List.class);
                        var auth = new UsernamePasswordAuthenticationToken(
                                "agent:" + claims.get("taskId", String.class), null,
                                permissions == null ? List.of()
                                        : permissions.stream().map(SimpleGrantedAuthority::new).toList());
                        auth.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                        SecurityContextHolder.getContext().setAuthentication(auth);
                    } else {
                        String username = jwtUtil.getUsername(token);
                        if (username != null) {
                            UserDetails userDetails = userDetailsService.loadUserByUsername(username);
                            var auth = new UsernamePasswordAuthenticationToken(
                                    userDetails, null, userDetails.getAuthorities());
                            auth.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                            SecurityContextHolder.getContext().setAuthentication(auth);
                        }
                    }
                }
            } catch (RuntimeException ignored) {
                // 无效 token：留空，由 AuthenticationEntryPoint 处理 401
            }
        }
        filterChain.doFilter(request, response);
    }
}
