package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentSuggestion;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentSuggestionRepository extends JpaRepository<AgentSuggestion, Long> {

    Page<AgentSuggestion> findAllByOrderByIdDesc(Pageable pageable);

    Optional<AgentSuggestion> findByGrantKey(String grantKey);

    /** 过期扫描：按状态取建议 */
    List<AgentSuggestion> findByStatus(String status);
}
