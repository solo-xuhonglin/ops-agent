package com.opsagent.admin.security;

import com.opsagent.admin.config.JwtProperties;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.List;

@Component
@RequiredArgsConstructor
public class JwtUtil {

    private final JwtProperties jwtProperties;

    private SecretKey key() {
        return Keys.hmacShaKeyFor(jwtProperties.getSecret().getBytes(StandardCharsets.UTF_8));
    }

    public String generateToken(String username, List<String> roles) {
        Date now = new Date();
        Date exp = new Date(now.getTime() + jwtProperties.getExpirationMs());
        return Jwts.builder()
                .subject(username)
                .claim("roles", roles)
                .issuedAt(now)
                .expiration(exp)
                .signWith(key())
                .compact();
    }

    public String generateRefreshToken(String username) {
        Date now = new Date();
        Date exp = new Date(now.getTime() + jwtProperties.getRefreshExpirationMs());
        return Jwts.builder()
                .subject(username)
                .claim("type", "refresh")
                .issuedAt(now)
                .expiration(exp)
                .signWith(key())
                .compact();
    }

    /**
     * 任务级 scoped token：权限裁剪（只读）+ 短时效，随 TaskDispatch 下发，agent 调工具时透传。
     * 不关联用户库，鉴权在 filter 内直接读 claims。
     */
    public String generateScopedToken(Long userId, List<String> permissions, String taskId, long ttlMs) {
        Date now = new Date();
        Date exp = new Date(now.getTime() + ttlMs);
        return Jwts.builder()
                .subject(userId == null ? "agent" : String.valueOf(userId))
                .claim("type", "scoped")
                .claim("permissions", permissions)
                .claim("taskId", taskId)
                .issuedAt(now)
                .expiration(exp)
                .signWith(key())
                .compact();
    }

    public boolean isScoped(String token) {
        return "scoped".equals(parse(token).get("type", String.class));
    }

    public Claims parse(String token) {
        return Jwts.parser()
                .verifyWith(key())
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    public String getUsername(String token) {
        return parse(token).getSubject();
    }

    public boolean isRefresh(String token) {
        return "refresh".equals(parse(token).get("type", String.class));
    }

    public boolean isExpired(String token) {
        return parse(token).getExpiration().before(new Date());
    }
}
