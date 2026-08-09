package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentSuggestion;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentSuggestionRepository extends JpaRepository<AgentSuggestion, Long> {

    Page<AgentSuggestion> findAllByOrderByIdDesc(Pageable pageable);

    Optional<AgentSuggestion> findBySuggestionId(String suggestionId);

    /** 审批动作：建议仍处于 PENDING 才可 approve/reject（条件更新防并发） */
    Optional<AgentSuggestion> findBySuggestionIdAndStatus(String suggestionId, String status);

    /** 过期扫描：按状态取建议 */
    List<AgentSuggestion> findByStatus(String status);

    /** plan 的步骤（按 step_no 升序） */
    List<AgentSuggestion> findByPlanIdOrderByStepNoAsc(String planId);
}
