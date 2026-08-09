package com.opsagent.admin.service.agent;

import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
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

/**
 * @RequireGrant 写端点强校验：
 * - 请求带 X-Agent-Worker（agent 代码注入的身份声明）→ 必须 scoped taskToken + 有效 grantKey（一次性消费）；
 * - 不带 → 用户 JWT 调用，走现有 @PreAuthorize（行为不变）；
 * - 任何"只有 scoped token 没有 grantKey"或伪造身份 → 403。
 * grantKey 只做有效性校验（存在即放行，不比对 action/targetId —— 授权 = 人工已确认该写操作）。
 */
@Aspect
@Component
@RequiredArgsConstructor
public class GrantCheckAspect {

    private final GrantService grantService;

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

        boolean ok = scoped && grantKey != null
                && grantService.consume(grantKey).isPresent();
        if (!ok) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    "agent write rejected: valid grant required (scoped token + grant key)");
        }
        return pjp.proceed();
    }
}
