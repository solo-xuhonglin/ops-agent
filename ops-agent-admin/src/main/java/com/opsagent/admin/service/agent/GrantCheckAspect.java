package com.opsagent.admin.service.agent;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.AgentSuggestion;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.AgentSuggestionRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.service.AuditLogService;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;
import java.util.Optional;

/**
 * @RequireGrant 写端点强校验：
 * - 请求带 X-Agent-Worker（agent 代码注入的身份声明）→ 必须 scoped taskToken + 有效 grantKey（一次性消费）；
 * - 不带 → 用户 JWT 调用，走现有 @PreAuthorize（行为不变）；
 * - 任何"只有 scoped token 没有 grantKey"或伪造身份 → 403。
 * grantKey 只做有效性校验（存在即放行，不比对 action/targetId —— 授权 = 人工已确认该写操作）。
 *
 * 消费成功后写审计：actor=Agent、审批人=建议确认人、params=建议参数、target=执行后返回 id。
 */
@Aspect
@Component
@RequiredArgsConstructor
@Slf4j
public class GrantCheckAspect {

    private final GrantService grantService;
    private final AuditLogService auditLogService;
    private final AgentSuggestionRepository suggestionRepository;
    private final UserRepository userRepository;

    @Around("@annotation(requireGrant)")
    public Object enforce(ProceedingJoinPoint pjp, RequireGrant requireGrant) throws Throwable {
        ServletRequestAttributes attrs = (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        if (attrs == null) {
            return pjp.proceed();
        }
        HttpServletRequest request = attrs.getRequest();
        String worker = request.getHeader("X-Agent-Worker");
        if (worker == null || worker.isBlank()) {
            // 非 agent 调用：用户 JWT 由 @PreAuthorize 鉴权
            return pjp.proceed();
        }

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        boolean scoped = auth != null && auth.getPrincipal() instanceof String p && p.startsWith("agent:");
        String grantKey = request.getHeader("X-Grant-Key");

        Optional<String> consumed = scoped && grantKey != null
                ? grantService.consume(grantKey) : Optional.empty();
        if (consumed.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    "agent write rejected: valid grant required (scoped token + grant key)");
        }

        // 消费成功 → 记录 agent 写操作审计（审批人/参数来自建议，target 取自执行结果）
        Object result = pjp.proceed();
        try {
            recordAgentAudit(requireGrant, consumed.get(), request, result);
        } catch (Exception e) {
            log.warn("agent audit failed: {}", e.getMessage());
        }
        return result;
    }

    private void recordAgentAudit(RequireGrant requireGrant, String suggestionId,
                                  HttpServletRequest request, Object result) {
        AgentSuggestion sug = suggestionRepository.findBySuggestionId(suggestionId).orElse(null);
        String params = (sug != null && sug.getParams() != null) ? sug.getParams() : null;
        String approver = null;
        if (sug != null && sug.getConfirmedBy() != null) {
            User u = userRepository.findById(sug.getConfirmedBy()).orElse(null);
            if (u != null) {
                approver = (u.getDisplayName() != null && !u.getDisplayName().isBlank())
                        ? u.getDisplayName() : u.getUsername();
            }
        }
        String action = requireGrant.action().replaceFirst("_", ":");
        Long targetId = resolveTargetId(result, request);
        String ip = clientIp(request);
        auditLogService.recordAgent(action, requireGrant.targetType(), targetId, params, ip, approver);
    }

    private Long resolveTargetId(Object result, HttpServletRequest request) {
        if (result instanceof ApiResponse<?> ar && ar.getData() instanceof Map<?, ?> m) {
            Object id = m.get("id");
            if (id instanceof Number n) {
                return n.longValue();
            }
        }
        // 路径含数字 id 时兜底（如 undeploy/{id}）
        String[] parts = request.getRequestURI().split("/");
        for (int i = parts.length - 1; i >= 0; i--) {
            if (parts[i].matches("\\d+")) {
                return Long.parseLong(parts[i]);
            }
        }
        return null;
    }

    private String clientIp(HttpServletRequest req) {
        String xff = req.getHeader("X-Forwarded-For");
        if (xff != null && !xff.isBlank()) {
            return xff.split(",")[0].trim();
        }
        return req.getRemoteAddr();
    }
}
