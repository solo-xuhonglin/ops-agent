package com.opsagent.admin.service;

import com.opsagent.admin.config.ServingProperties;
import com.opsagent.admin.entity.ServingEndpoint;
import com.opsagent.admin.repository.ServingEndpointRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;

/**
 * serving 容器健康管理：
 * 1) 部署后就绪轮询（CREATING → DEPLOYED / FAILED，超时清理容器）
 * 2) 运行期探活（DEPLOYED → UNHEALTHY，恢复回 DEPLOYED）
 * 与训练轮询同样的"容器保持哑、admin 轮询归集"模式。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class ServingHealthPoller {

    private static final List<String> ACTIVE = List.of("CREATING");
    private static final List<String> LIVE = List.of("DEPLOYED", "UNHEALTHY");

    private final ServingEndpointRepository servingEndpointRepository;
    private final ServingLauncher servingLauncher;
    private final ServingProperties servingProperties;

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofMillis(3000))
            .build();

    // endpointId -> 探活失败累计（并发安全，避免多线程并发读写下标不一致）
    private final ConcurrentHashMap<Long, Integer> healthFailures = new ConcurrentHashMap<>();

    /** 部署后就绪轮询：每 2s 扫一次 CREATING 中的 endpoint */
    @Scheduled(fixedDelay = 2000)
    public void pollReady() {
        if (!servingProperties.isEnabled()) return;
        List<ServingEndpoint> creating = servingEndpointRepository.findAll().stream()
                .filter(ep -> "CREATING".equals(ep.getStatus()))
                .toList();
        for (ServingEndpoint ep : creating) {
            try {
                checkReady(ep);
            } catch (Exception e) {
                log.warn("Serving ready poll error endpointId={} error={}", ep.getId(), e.getMessage());
            }
        }
    }

    /** 运行期探活：固定间隔扫 DEPLOYED/UNHEALTHY 中的 endpoint */
    @Scheduled(fixedDelayString = "${serving.health-check-interval-ms:30000}")
    public void pollHealth() {
        if (!servingProperties.isEnabled()) return;
        List<ServingEndpoint> live = servingEndpointRepository.findAll().stream()
                .filter(ep -> LIVE.contains(ep.getStatus()))
                .toList();
        for (ServingEndpoint ep : live) {
            try {
                checkAlive(ep);
            } catch (Exception e) {
                log.warn("Serving health poll error endpointId={} error={}", ep.getId(), e.getMessage());
            }
        }
    }

    private void checkReady(ServingEndpoint ep) {
        if (isHealthy(ep)) {
            ep.setStatus("DEPLOYED");
            ep.setUnhealthyCount(0);
            servingEndpointRepository.save(ep);
            log.info("Serving endpoint ready endpointId={} modelVersionId={}", ep.getId(), ep.getModelVersionId());
            return;
        }
        // 超时判定（从 createdAt 起算）
        if (ep.getCreatedAt() != null && ep.getCreatedAt()
                .plusSeconds(servingProperties.getReadyTimeoutSeconds()).isBefore(OffsetDateTime.now())) {
            servingLauncher.stopAndRemove(ep.getId());
            ep.setStatus("FAILED");
            servingEndpointRepository.save(ep);
            log.warn("Serving endpoint not ready in {}s, marked FAILED endpointId={}",
                    servingProperties.getReadyTimeoutSeconds(), ep.getId());
        }
    }

    private void checkAlive(ServingEndpoint ep) {
        boolean healthy = isHealthy(ep);
        int failures = healthFailures.merge(ep.getId(), healthy ? 0 : 1, (old, v) -> healthy ? 0 : old + v);
        if (healthy) {
            if (!"DEPLOYED".equals(ep.getStatus())) {
                ep.setStatus("DEPLOYED");
                ep.setUnhealthyCount(0);
                servingEndpointRepository.save(ep);
                log.info("Serving endpoint recovered endpointId={}", ep.getId());
            }
        } else if (failures >= servingProperties.getUnhealthyThreshold()
                && !"UNHEALTHY".equals(ep.getStatus())) {
            ep.setStatus("UNHEALTHY");
            ep.setUnhealthyCount(failures);
            servingEndpointRepository.save(ep);
            log.warn("Serving endpoint unhealthy ({} consecutive failures) endpointId={}", failures, ep.getId());
        }
    }

    private boolean isHealthy(ServingEndpoint ep) {
        String url = ep.getUrl();
        if (url == null || url.isBlank()) return false;
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url + "/health"))
                    .timeout(Duration.ofMillis(servingProperties.getHttpTimeoutMillis()))
                    .GET()
                    .build();
            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            return resp.statusCode() == 200;
        } catch (Exception e) {
            return false;
        }
    }
}
