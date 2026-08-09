package com.opsagent.admin.repository;

import com.opsagent.admin.entity.Conversation;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface ConversationRepository extends JpaRepository<Conversation, Long> {

    Optional<Conversation> findByConversationId(String conversationId);

    /** 某用户的会话列表（新→旧）；userId 为 null 时只查未归属会话（系统内部创建） */
    Page<Conversation> findByUserIdOrderByUpdatedAtDesc(Long userId, Pageable pageable);

    Page<Conversation> findAllByOrderByUpdatedAtDesc(Pageable pageable);
}
