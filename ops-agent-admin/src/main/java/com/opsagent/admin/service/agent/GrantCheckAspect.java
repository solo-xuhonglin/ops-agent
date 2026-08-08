package com.opsagent.admin.service.agent;

import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

/**
 * @RequireGrant 写端点强校验：
 * - 请求带 X-Agent-Worker（agent 代码注入的身份声明）→ 必须 scoped taskToken + 有效 grantKey（action/targetId 匹配，原子消费）；
 * - 不带 → 用户 JWT 调用，走现有 @PreAuthorize（行为不变）；
 * - 任何"只有 scoped token 没有 grantKey"或伪造身份 → 403。
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
        Long targetId = resolveTargetId(pjp, requireGrant.targetParam());

        boolean ok = scoped && grantKey != null && targetId != null
                && grantService.consumeAndMatch(grantKey, requireGrant.action(), targetId).isPresent();
        if (!ok) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    "agent write rejected: valid grant required (scoped token + grant key)");
        }
        return pjp.proceed();
    }

    /** 按 targetParam（逻辑字段名）从方法实参提取 targetId：
     *  ① 参数名 == targetParam 的 Number/String 参数；② DTO 的 get{TargetParam}()；③ Map 的 key。 */
    private Long resolveTargetId(ProceedingJoinPoint pjp, String targetParam) {
        Object[] args = pjp.getArgs();
        String[] names = ((MethodSignature) pjp.getSignature()).getParameterNames();
        for (int i = 0; i < args.length; i++) {
            Object arg = args[i];
            String name = names != null && i < names.length ? names[i] : "";
            if (targetParam.equals(name) && arg instanceof Number n) {
                return n.longValue();
            }
            if (targetParam.equals(name) && arg instanceof String s) {
                return parseLong(s);
            }
            if (arg instanceof Map<?, ?> map && map.containsKey(targetParam)) {
                return parseLong(String.valueOf(map.get(targetParam)));
            }
            if (arg != null && !(arg instanceof CharSequence) && !(arg instanceof Number)) {
                try {
                    String getter = "get" + Character.toUpperCase(targetParam.charAt(0)) + targetParam.substring(1);
                    Object value = arg.getClass().getMethod(getter).invoke(arg);
                    return value instanceof Number n ? n.longValue() : parseLong(String.valueOf(value));
                } catch (ReflectiveOperationException | IllegalArgumentException ignored) {
                    // 该参数无此字段，继续
                }
            }
        }
        return null;
    }

    private Long parseLong(String s) {
        try {
            return Long.parseLong(s.trim());
        } catch (NumberFormatException | NullPointerException e) {
            return null;
        }
    }
}
