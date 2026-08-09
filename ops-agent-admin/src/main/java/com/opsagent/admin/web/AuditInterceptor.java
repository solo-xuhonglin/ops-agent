package com.opsagent.admin.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.security.CurrentUser;
import com.opsagent.admin.service.AuditLogService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.util.ContentCachingRequestWrapper;

import java.io.UnsupportedEncodingException;
import java.util.List;
import java.util.Map;

/**
 * 人类写操作审计拦截器：在请求成功完成后，自动记录 /api/** 下变更类操作的执行人、操作、参数、IP。
 * agent 写操作由 GrantCheckAspect 记录（带 X-Agent-Worker 头时此处跳过，避免重复）。
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class AuditInterceptor implements HandlerInterceptor {

    private final AuditLogService auditLogService;
    private final ObjectMapper objectMapper;
    private final CurrentUser currentUser;

    private static final List<String> MUTATING = List.of("POST", "PUT", "DELETE", "PATCH");

    /** 敏感字段脱敏（请求体可能含密码/token，禁止入库） */
    private static final List<String> SENSITIVE = List.of(
            "password", "passwordhash", "password_hash",
            "token", "refreshtoken", "refresh_token",
            "grantkey", "grant_key", "secret", "secretkey", "secret_key");

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                 Object handler, Exception ex) {
        try {
            if (!isAuditable(request, response)) {
                return;
            }
            String action = ActionMapper.resolve(request.getMethod(), request.getRequestURI());
            String targetType = ActionMapper.targetTypeOf(request.getMethod(), request.getRequestURI());
            Long targetId = extractPathId(request);
            String params = readAndRedactBody(request);
            String ip = clientIp(request);
            String actor = currentUser.username();
            if (actor == null || actor.isBlank()) {
                actor = "unknown";
            }
            auditLogService.recordHuman(action, actor, targetType, targetId, params, ip);
        } catch (Exception e) {
            log.warn("audit interceptor failed: {}", e.getMessage());
        }
    }

    private boolean isAuditable(HttpServletRequest req, HttpServletResponse res) {
        if (!MUTATING.contains(req.getMethod().toUpperCase())) {
            return false;
        }
        String uri = req.getRequestURI();
        if (!uri.startsWith("/api/") || uri.startsWith("/api/auth/")) {
            return false;
        }
        // agent 写操作由 GrantCheckAspect 记录
        if (req.getHeader("X-Agent-Worker") != null) {
            return false;
        }
        int status = res.getStatus();
        return status >= 200 && status < 300;
    }

    private Long extractPathId(HttpServletRequest req) {
        String[] parts = req.getRequestURI().split("/");
        for (int i = parts.length - 1; i >= 0; i--) {
            String p = parts[i];
            if (!p.isBlank() && p.matches("\\d+")) {
                return Long.parseLong(p);
            }
        }
        return null;
    }

    private String readAndRedactBody(HttpServletRequest req) {
        byte[] content;
        if (req instanceof ContentCachingRequestWrapper wrapper) {
            content = wrapper.getContentAsByteArray();
        } else {
            return null;
        }
        if (content == null || content.length == 0) {
            return null;
        }
        try {
            String raw = new String(content, req.getCharacterEncoding() != null
                    ? req.getCharacterEncoding() : "UTF-8");
            Object parsed = objectMapper.readValue(raw, Object.class);
            redact(parsed);
            return objectMapper.writeValueAsString(parsed);
        } catch (UnsupportedEncodingException e) {
            return null;
        } catch (Exception e) {
            // 非 JSON 体：原样截断存储
            String s = new String(content, java.nio.charset.StandardCharsets.UTF_8);
            return s.length() > 2000 ? s.substring(0, 2000) : s;
        }
    }

    /** 递归遍历 JSON 结构，将敏感字段值替换为 *** */
    @SuppressWarnings("unchecked")
    private void redact(Object node) {
        if (node instanceof Map<?, ?> map) {
            Map<String, Object> m = (Map<String, Object>) map;
            for (Map.Entry<String, Object> e : m.entrySet()) {
                if (SENSITIVE.contains(e.getKey().toLowerCase())) {
                    m.put(e.getKey(), "***");
                } else {
                    redact(e.getValue());
                }
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) {
                redact(item);
            }
        }
    }

    private String clientIp(HttpServletRequest req) {
        String xff = req.getHeader("X-Forwarded-For");
        if (xff != null && !xff.isBlank()) {
            return xff.split(",")[0].trim();
        }
        return req.getRemoteAddr();
    }
}
