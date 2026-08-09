package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentTask;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.OffsetDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface AgentTaskRepository extends JpaRepository<AgentTask, Long> {

    Optional<AgentTask> findByTaskId(String taskId);

    Page<AgentTask> findAllByOrderByIdDesc(Pageable pageable);

    /** 超时扫描：卡在 DISPATCHED/RUNNING 且超时的任务 */
    List<AgentTask> findByStatusInAndCreatedAtBefore(Collection<String> statuses, OffsetDateTime before);

    /** 建议过期判定：某建议的 execute_suggestion 任务是否仍在执行（status IN 且 query 含 suggestionId 片段） */
    List<AgentTask> findByTaskTypeAndQueryContainingAndStatusIn(
            String taskType, String queryFragment, Collection<String> statuses);
}
