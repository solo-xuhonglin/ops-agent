package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentTool;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentToolRepository extends JpaRepository<AgentTool, Long> {

    Optional<AgentTool> findByName(String name);

    /** 注册时下发启用的只读工具（写工具 M3 接入 grantKey 后下发） */
    List<AgentTool> findByEnabledTrueAndIsWriteFalseOrderByIdAsc();
}
