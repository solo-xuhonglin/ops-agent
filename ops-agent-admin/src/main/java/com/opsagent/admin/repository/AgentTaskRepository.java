package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentTask;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface AgentTaskRepository extends JpaRepository<AgentTask, Long> {

    Optional<AgentTask> findByTaskId(String taskId);

    Page<AgentTask> findAllByOrderByIdDesc(Pageable pageable);

    /** 建议过期判定：该建议的 execute 任务是否仍在执行（status IN） */
    List<AgentTask> findBySuggestionIdAndStatusIn(String suggestionId, Collection<String> statuses);

    /** 会话级任务列表（前端对话侧边栏/详情） */
    List<AgentTask> findByConversationIdOrderByIdDesc(String conversationId);
}
