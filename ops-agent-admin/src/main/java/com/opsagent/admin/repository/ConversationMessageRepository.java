package com.opsagent.admin.repository;

import com.opsagent.admin.entity.ConversationMessage;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface ConversationMessageRepository extends JpaRepository<ConversationMessage, Long> {

    List<ConversationMessage> findByConversationIdOrderByIdAsc(String conversationId);

    /** 该会话已完成的历史消息（用于组装多轮上下文，新→旧取前 N 条后再反转） */
    List<ConversationMessage> findByConversationIdAndStatusInOrderByIdDesc(
            String conversationId, List<String> statuses);

    /** 该轮内部任务对应的 assistant 消息（落库定位；理论上每轮一条） */
    Optional<ConversationMessage> findFirstByTaskId(String taskId);

    void deleteByConversationId(String conversationId);
}
