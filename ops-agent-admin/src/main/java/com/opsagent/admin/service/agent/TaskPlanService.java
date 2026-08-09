package com.opsagent.admin.service.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.agent.proto.PlanStep;
import com.opsagent.admin.entity.TaskPlan;
import com.opsagent.admin.repository.TaskPlanRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * 任务计划（Plan）持久化：agent 通过 gRPC TaskPlan 上报建/更新，admin 仅落库（不做流程控制）。
 * steps 序列化为 JSON 数组存储，读取时还原为列表供 agent_task_list 工具返回。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class TaskPlanService {

    private final TaskPlanRepository repository;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Transactional
    public TaskPlan upsert(com.opsagent.admin.agent.proto.TaskPlan proto) {
        String conversationId = proto.getConversationId();
        if (conversationId == null || conversationId.isBlank()) {
            return null; // 非会话任务，忽略
        }
        TaskPlan plan = repository.findByConversationId(conversationId).orElse(new TaskPlan());
        plan.setConversationId(conversationId);
        plan.setPlanId(proto.getPlanId());
        plan.setSummary(proto.getSummary());
        if (!proto.getStatus().isBlank()) {
            plan.setStatus(proto.getStatus());
        }
        plan.setStepsJson(toJson(proto.getStepsList()));
        repository.save(plan);
        log.info("task plan saved: conversation={}, plan={}, steps={}",
                conversationId, proto.getPlanId(), proto.getStepsCount());
        return plan;
    }

    @Transactional(readOnly = true)
    public Optional<TaskPlan> findByConversationId(String conversationId) {
        return repository.findByConversationId(conversationId);
    }

    private String toJson(List<PlanStep> steps) {
        List<Map<String, Object>> list = new ArrayList<>();
        for (PlanStep s : steps) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("action_type", s.getActionType());
            m.put("target_type", s.getTargetType());
            m.put("target_id", s.getTargetId());
            m.put("params", s.getParams());
            m.put("reason", s.getReason());
            m.put("priority", s.getPriority());
            m.put("status", s.getStatus());
            m.put("object_type", s.getObjectType());
            m.put("object_id", s.getObjectId());
            list.add(m);
        }
        try {
            return objectMapper.writeValueAsString(list);
        } catch (Exception e) {
            log.warn("plan steps serialize failed: {}", e.getMessage());
            return "[]";
        }
    }
}