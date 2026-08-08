package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentEvent;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface AgentEventRepository extends JpaRepository<AgentEvent, Long> {

    List<AgentEvent> findByTaskIdOrderBySeqAsc(String taskId);
}
