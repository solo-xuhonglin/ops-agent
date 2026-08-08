package com.opsagent.admin.repository;

import com.opsagent.admin.entity.AgentTool;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface AgentToolRepository extends JpaRepository<AgentTool, Long> {

    Optional<AgentTool> findByName(String name);

    /** 注册时下发启用的只读工具（写工具待 M3 grantKey 机制接入） */
    List<AgentTool> findByEnabledTrueAndIsWriteFalseOrderByIdAsc();

    /** 下发全部启用工具（含写工具；agent 侧无 grantKey 时不执行写操作） */
    List<AgentTool> findByEnabledTrueOrderByIdAsc();

    /** 管理面列表（人用） */
    List<AgentTool> findAllByOrderByIdAsc();
}
