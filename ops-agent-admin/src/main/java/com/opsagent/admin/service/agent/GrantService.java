package com.opsagent.admin.service.agent;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.Optional;
import java.util.UUID;

/**
 * 处置授权 grantKey 服务（Redis 权威）：
 * - issue：人工确认时签发，TTL 过期自动作废；
 * - consumeAndMatch：agent 执行写操作时原子消费（GETDEL），并校验 action/target 匹配（一次性防重放）。
 */
@Service
@Slf4j
public class GrantService {

    private final StringRedisTemplate redis;
    private final long ttlSeconds;
    private final String keyPrefix;

    public GrantService(StringRedisTemplate redis,
                        @Value("${agent.grant.ttl-seconds:600}") long ttlSeconds,
                        @Value("${agent.grant.key-prefix:agent:grant:}") String keyPrefix) {
        this.redis = redis;
        this.ttlSeconds = ttlSeconds;
        this.keyPrefix = keyPrefix;
    }

    /** 签发 grantKey（完整 key 含前缀，agent 原样回传）。 */
    public String issue(String actionType, String targetType, Long targetId, Long suggestionId) {
        String grantKey = keyPrefix + UUID.randomUUID();
        GrantMeta meta = new GrantMeta(actionType, targetType, targetId, suggestionId);
        redis.opsForValue().set(grantKey, meta.encode(), Duration.ofSeconds(ttlSeconds));
        log.info("grant issued: key={} action={} target={}/{} ttl={}s",
                grantKey, actionType, targetType, targetId, ttlSeconds);
        return grantKey;
    }

    public long ttlSeconds() {
        return ttlSeconds;
    }

    /** 探活（不消费）：key 是否仍在 Redis（未过期、未消费）。供过期扫描判定。 */
    public boolean exists(String grantKey) {
        return grantKey != null && !grantKey.isBlank()
                && Boolean.TRUE.equals(redis.hasKey(grantKey));
    }

    /** 不消费查 suggestionId：agent 创建训练/serving 时把 suggestionId 回写到 job，供后续 followup 反查 conversation。 */
    public Optional<Long> getSuggestionId(String grantKey) {
        if (grantKey == null || grantKey.isBlank()) {
            return Optional.empty();
        }
        String raw = redis.opsForValue().get(grantKey); // 仅读，不 GETDEL
        if (raw == null) {
            return Optional.empty();
        }
        try {
            return Optional.of(GrantMeta.decode(raw).suggestionId());
        } catch (Exception e) {
            log.warn("grant decode failed (peek only): key={} err={}", grantKey, e.getMessage());
            return Optional.empty();
        }
    }

    /**
     * 原子消费并校验（action + targetId 匹配，targetType 由 action 隐含不作硬比对）。
     * 返回匹配的 suggestionId；key 不存在（超时/已消费）或 action/targetId 不匹配返回空。
     */
    public Optional<Long> consumeAndMatch(String grantKey, String actionType, Long targetId) {
        if (grantKey == null || grantKey.isBlank()) {
            return Optional.empty();
        }
        String raw = redis.opsForValue().getAndDelete(grantKey); // 原子：无论匹配与否都消费
        if (raw == null) {
            log.warn("grant consume miss (expired/already used): {}", grantKey);
            return Optional.empty();
        }
        GrantMeta meta = GrantMeta.decode(raw);
        if (meta.actionType().equals(actionType) && meta.targetId().equals(targetId)) {
            log.info("grant consumed: key={} suggestionId={}", grantKey, meta.suggestionId());
            return Optional.of(meta.suggestionId());
        }
        log.warn("grant mismatch: expected action={} targetId={} got action={} targetId={}",
                actionType, targetId, meta.actionType(), meta.targetId());
        return Optional.empty();
    }

    /** value 编码：suggestionId|actionType|targetType|targetId */
    private record GrantMeta(String actionType, String targetType, Long targetId, Long suggestionId) {
        String encode() {
            return suggestionId + "|" + actionType + "|" + targetType + "|" + targetId;
        }

        static GrantMeta decode(String raw) {
            String[] parts = raw.split("\\|", -1);
            return new GrantMeta(parts.length > 1 ? parts[1] : "",
                    parts.length > 2 ? parts[2] : "",
                    parts.length > 3 ? Long.parseLong(parts[3]) : 0L,
                    parts.length > 0 ? Long.parseLong(parts[0]) : 0L);
        }
    }
}
