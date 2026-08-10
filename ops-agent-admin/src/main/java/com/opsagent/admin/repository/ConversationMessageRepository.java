package com.opsagent.admin.repository;

import com.opsagent.admin.entity.ConversationMessage;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface ConversationMessageRepository extends JpaRepository<ConversationMessage, Long> {

    List<ConversationMessage> findByConversationIdOrderByIdAsc(String conversationId);

    /** 该会话已完成的历史消息（用于组装多轮上下文，新→旧取前 N 条后再反转） */
    List<ConversationMessage> findByConversationIdAndStatusInOrderByIdDesc(
            String conversationId, List<String> statuses);

    void deleteByConversationId(String conversationId);

    /**
     * APPROVAL upsert 锚点：以 conversation_id + 解析 payload_json.suggestionId 定位唯一行。
     * 用原生 SQL 抽 JSON 字段，避免 entity 多加一列（payloadJson 已存 suggestionId 即可覆盖后续状态）。
     */
    @Query(value = "SELECT * FROM agent_conversation_messages "
            + "WHERE conversation_id = :conv AND kind = 'APPROVAL' "
            + "AND payload_json::jsonb ->> 'suggestionId' = :suggestionId "
            + "ORDER BY id DESC LIMIT 1",
            nativeQuery = true)
    Optional<ConversationMessage> findFirstByPayloadSuggestionId(
            @Param("conv") String conversationId,
            @Param("suggestionId") String suggestionId);
}