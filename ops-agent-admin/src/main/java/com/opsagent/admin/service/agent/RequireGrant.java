package com.opsagent.admin.service.agent;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 写端点强校验：agent 调用（带 X-Agent-Worker）必须同时具备 scoped taskToken + 有效 grantKey
 * （一次性消费，不比对 action/targetId —— 授权 = 人工已确认该写操作）。
 * 用户 JWT 调用不受影响（由 @PreAuthorize 负责）。
 *
 * @param action     对应写工具名（如 serving_undeploy），用于日志/审计
 * @param targetType 目标资源类型（serving_endpoint / training_job / dataset），用于记录
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RequireGrant {
    String action();

    String targetType();
}
