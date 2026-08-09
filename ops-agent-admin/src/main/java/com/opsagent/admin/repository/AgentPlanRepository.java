package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentPlan;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentPlanRepository extends JpaRepository<AgentPlan, Long> {

    Optional<AgentPlan> findByPlanId(String planId);

    /** 会话的活跃 plan（PLANNED/RUNNING，取最新） */
    Optional<AgentPlan> findFirstByConversationIdAndStatusInOrderByIdDesc(
            String conversationId, java.util.Collection<String> statuses);

    List<AgentPlan> findByConversationIdOrderByIdDesc(String conversationId);
}
