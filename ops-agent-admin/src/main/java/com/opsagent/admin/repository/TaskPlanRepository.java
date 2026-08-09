package com.opsagent.admin.repository;

import com.opsagent.admin.entity.TaskPlan;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface TaskPlanRepository extends JpaRepository<TaskPlan, Long> {

    Optional<TaskPlan> findByConversationId(String conversationId);
}