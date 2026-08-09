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
 * - consume：agent 执行写操作时原子消费（GETDEL，一次性防重放），有效即放行。
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

    /** 签发 grantKey（完整 key 含前缀，agent 原样回传）。suggestionId 为 UUID 业务标识。 */
    public String issue(String actionType, String targetType, Long targetId, String suggestionId) {
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

    /**
     * 原子消费授权（一次性，防重放）：grantKey 存在（未过期/未消费）即放行，
     * 不比对 action/targetId —— 授权语义 = 人工已确认该写操作，agent 执行时凭证有效即可
     * （scoped taskToken 已绑定任务，grantKey 随 TaskDispatch 下发，两者共同约束调用面）。
     * 返回 suggestionId（业务标识，供审计）。
     */
    public Optional<String> consume(String grantKey) {
        if (grantKey == null || grantKey.isBlank()) {
            return Optional.empty();
        }
        String raw = redis.opsForValue().getAndDelete(grantKey); // 原子：无论匹配与否都消费
        if (raw == null) {
            log.warn("grant consume miss (expired/already used): {}", grantKey);
            return Optional.empty();
        }
        GrantMeta meta = GrantMeta.decode(raw);
        log.info("grant consumed: key={} suggestionId={}", grantKey, meta.suggestionId());
        return Optional.of(meta.suggestionId());
    }

    /** value 编码：suggestionId|actionType|targetType|targetId */
    private record GrantMeta(String actionType, String targetType, Long targetId, String suggestionId) {
        String encode() {
            return suggestionId + "|" + actionType + "|" + targetType + "|" + targetId;
        }

        static GrantMeta decode(String raw) {
            String[] parts = raw.split("\\|", -1);
            return new GrantMeta(parts.length > 1 ? parts[1] : "",
                    parts.length > 2 ? parts[2] : "",
                    parts.length > 3 ? parseLong(parts[3]) : 0L,
                    parts.length > 0 ? parts[0] : "");
        }

        private static Long parseLong(String s) {
            try {
                return Long.parseLong(s);
            } catch (NumberFormatException e) {
                return 0L;
            }
        }
    }
}
