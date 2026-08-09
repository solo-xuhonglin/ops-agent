package com.opsagent.admin.service;

import com.opsagent.admin.entity.AuditLog;
import com.opsagent.admin.repository.AuditLogRepository;
import jakarta.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;

/**
 * 审计日志写入（异步 fire-and-forget，不阻塞业务主流程）与查询。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class AuditLogService {

    private final AuditLogRepository auditLogRepository;

    /** 人类写操作记录 */
    public void recordHuman(String action, String actorName, String targetType,
                            Long targetId, String params, String ip) {
        AuditLog log = new AuditLog();
        log.setAction(action);
        log.setActorType("USER");
        log.setActorName(actorName);
        log.setTargetType(targetType);
        log.setTargetId(targetId);
        log.setParams(params);
        log.setIp(ip);
        saveAsync(log);
    }

    /** agent 写操作记录（actor = Agent，审批人单列） */
    public void recordAgent(String action, String targetType, Long targetId,
                            String params, String ip, String approverName) {
        AuditLog log = new AuditLog();
        log.setAction(action);
        log.setActorType("AGENT");
        log.setActorName("Agent");
        log.setApproverName(approverName);
        log.setTargetType(targetType);
        log.setTargetId(targetId);
        log.setParams(params);
        log.setIp(ip);
        saveAsync(log);
    }

    private void saveAsync(AuditLog log) {
        CompletableFuture.runAsync(() -> {
            try {
                auditLogRepository.save(log);
            } catch (Exception e) {
                log.warn("audit log save failed action={} actor={} error={}",
                        log.getAction(), log.getActorName(), e.getMessage());
            }
        });
    }

    public Page<AuditLog> search(String action, String actorType, String actorName,
                                 String approverName, String targetType,
                                 OffsetDateTime from, OffsetDateTime to, Pageable pageable) {
        Specification<AuditLog> spec = (root, query, cb) -> {
            List<Predicate> preds = new ArrayList<>();
            if (action != null && !action.isBlank()) {
                preds.add(cb.equal(root.get("action"), action));
            }
            if (actorType != null && !actorType.isBlank()) {
                preds.add(cb.equal(root.get("actorType"), actorType));
            }
            if (actorName != null && !actorName.isBlank()) {
                preds.add(cb.like(root.get("actorName"), "%" + actorName + "%"));
            }
            if (approverName != null && !approverName.isBlank()) {
                preds.add(cb.like(root.get("approverName"), "%" + approverName + "%"));
            }
            if (targetType != null && !targetType.isBlank()) {
                preds.add(cb.equal(root.get("targetType"), targetType));
            }
            if (from != null) {
                preds.add(cb.greaterThanOrEqualTo(root.get("createdAt"), from));
            }
            if (to != null) {
                preds.add(cb.lessThanOrEqualTo(root.get("createdAt"), to));
            }
            return cb.and(preds.toArray(new Predicate[0]));
        };
        return auditLogRepository.findAll(spec, pageable);
    }
}
