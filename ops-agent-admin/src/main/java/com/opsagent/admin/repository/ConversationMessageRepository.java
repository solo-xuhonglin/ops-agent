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

    /** 该轮内部任务对应的 assistant 消息（落库定位；理论上每轮一条） */
    Optional<ConversationMessage> findFirstByTaskId(String taskId);

    /** 流内 assistant 轮次行的 upsert 锚点（按 messageId 定位唯一行） */
    Optional<ConversationMessage> findFirstByMessageId(String messageId);

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

    /**
     * TOOL_CALL upsert 锚点：按 tool_call_id 定位唯一行（同一 LLM 原生 tool_call 共享 call_id）。
     * 历史渲染按 callId 唯一合并，避免 call/result 拆两行难读。
     */
    @Query(value = "SELECT * FROM agent_conversation_messages "
            + "WHERE conversation_id = :conv AND kind = 'TOOL_CALL' "
            + "AND tool_call_id = :callId "
            + "ORDER BY id DESC LIMIT 1",
            nativeQuery = true)
    Optional<ConversationMessage> findFirstByToolCallId(
            @Param("conv") String conversationId,
            @Param("callId") String toolCallId);
}
