package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentTask;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface AgentTaskRepository extends JpaRepository<AgentTask, Long> {

    Optional<AgentTask> findByTaskId(String taskId);

    Page<AgentTask> findAllByOrderByIdDesc(Pageable pageable);
}
