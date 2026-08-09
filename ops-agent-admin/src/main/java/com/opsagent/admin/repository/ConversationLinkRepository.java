package com.opsagent.admin.repository;

import com.opsagent.admin.entity.ConversationLink;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface ConversationLinkRepository extends JpaRepository<ConversationLink, Long> {

    /** followup 反查：某个对象（如训练 job）由哪个会话发起 */
    Optional<ConversationLink> findFirstByObjectTypeAndObjectId(String objectType, Long objectId);

    /** 待 followup 的对象（追踪层状态，供 TrainingFollowupService 独立扫描） */
    java.util.List<ConversationLink> findByObjectTypeAndFollowupDispatchedFalse(String objectType);

    /** 会话下的写操作关联（审计/追溯） */
    java.util.List<ConversationLink> findByConversationIdOrderByIdDesc(String conversationId);
}