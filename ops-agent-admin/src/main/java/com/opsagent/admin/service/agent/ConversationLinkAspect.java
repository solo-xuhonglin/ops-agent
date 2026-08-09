package com.opsagent.admin.service.agent;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.entity.ConversationLink;
import com.opsagent.admin.repository.AgentTaskRepository;
import com.opsagent.admin.repository.ConversationLinkRepository;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

/**
 * 会话关联切面：agent 执行已审批写操作（@RequireGrant 接口）成功后，自动记录
 * 「创建出的对象 ↔ 发起会话」关系到 conversation_links 表。业务代码无需感知
 * conversation_id，异步 followup（训练完成 → 推部署建议回原会话）从关系表反查。
 */
@Aspect
@Component
@RequiredArgsConstructor
@Slf4j
public class ConversationLinkAspect {

    private final ConversationLinkRepository linkRepository;
    private final AgentTaskRepository agentTaskRepository;

    @Around("@annotation(requireGrant)")
    public Object link(ProceedingJoinPoint pjp, RequireGrant requireGrant) throws Throwable {
        ServletRequestAttributes attrs = (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        if (attrs == null) {
            return pjp.proceed();
        }
        HttpServletRequest request = attrs.getRequest();
        String taskId = request.getHeader("X-Agent-Task");
        if (taskId == null || taskId.isBlank()) {
            return pjp.proceed(); // 用户直接调用，无任务上下文，不记录
        }
        Object result = pjp.proceed();
        // 只记录创建型写操作的成功响应（对象 ID 从响应 data 提取）
        if (result instanceof ApiResponse<?> resp && resp.getData() != null) {
            Long objectId = extractId(resp.getData());
            String conversationId = agentTaskRepository.findByTaskId(taskId)
                    .map(AgentTask::getConversationId).orElse(null);
            if (objectId != null && conversationId != null && !conversationId.isBlank()) {
                ConversationLink link = new ConversationLink();
                link.setConversationId(conversationId);
                link.setTaskId(taskId);
                link.setActionType(requireGrant.action());
                link.setObjectType(requireGrant.targetType());
                link.setObjectId(objectId);
                linkRepository.save(link);
                log.info("conversation link recorded: conv={} task={} action={} object={}/{}",
                        conversationId, taskId, requireGrant.action(),
                        requireGrant.targetType(), objectId);
            }
        }
        return result;
    }

    /** 从 ApiResponse.data 提取对象 ID（data 为 Number 或带 getId() 的实体）。 */
    private Long extractId(Object data) {
        if (data instanceof Number n) {
            return n.longValue();
        }
        try {
            Object value = data.getClass().getMethod("getId").invoke(data);
            return value instanceof Number n ? n.longValue() : null;
        } catch (Exception e) {
            return null;
        }
    }
}