package com.opsagent.admin.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.util.ContentCachingRequestWrapper;

import java.io.IOException;
import java.util.Set;

/**
 * 为可审计的写请求（/api/** 下 POST/PUT/DELETE/PATCH，排除 /api/auth）包裹
 * ContentCachingRequestWrapper，使 AuditInterceptor 在 afterCompletion 阶段仍能读取请求体（参数）。
 * 必须在最外层（早于 Security / DispatcherServlet）包裹，避免请求体流被提前消费。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class CachingRequestFilter implements jakarta.servlet.Filter {

    private static final Set<String> MUTATING = Set.of("POST", "PUT", "DELETE", "PATCH");

    @Override
    public void doFilter(jakarta.servlet.ServletRequest request,
                         jakarta.servlet.ServletResponse response,
                         FilterChain chain) throws IOException, ServletException {
        if (request instanceof HttpServletRequest req && shouldCache(req)) {
            chain.doFilter(new ContentCachingRequestWrapper(req), response);
        } else {
            chain.doFilter(request, response);
        }
    }

    private boolean shouldCache(HttpServletRequest req) {
        if (!MUTATING.contains(req.getMethod().toUpperCase())) {
            return false;
        }
        String uri = req.getRequestURI();
        return uri.startsWith("/api/") && !uri.startsWith("/api/auth/");
    }
}
