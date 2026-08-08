package com.opsagent.admin.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.ServingEndpoint;
import com.opsagent.admin.service.ServingEndpointService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/**
 * serving 推理代理：外部（前端 / 未来 agent）统一经本控制器调用部署好的 LSTM 模型。
 * 鉴权复用 admin 的 JWT + RBAC（serving:read），serving 容器自身不设鉴权，仅内网可达。
 */
@RestController
@RequestMapping("/api/serving-proxy")
@RequiredArgsConstructor
@Slf4j
public class ServingProxyController {

    private static final Duration TIMEOUT = Duration.ofSeconds(15);

    private final ServingEndpointService servingEndpointService;
    private final ObjectMapper objectMapper;

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    @PostMapping("/{endpointId}/predict")
    @PreAuthorize("hasAuthority('serving:read')")
    public ApiResponse<?> predict(@PathVariable Long endpointId,
                                  @RequestBody Map<String, Object> body) {
        ServingEndpoint ep = servingEndpointService.get(endpointId);
        if (!"DEPLOYED".equals(ep.getStatus())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "部署端点未就绪（状态: " + ep.getStatus() + "）");
        }
        String url = ep.getUrl();
        if (url == null || url.isBlank()) {
            throw new IllegalStateException("部署端点缺少访问地址");
        }
        return ApiResponse.ok(forward(url + "/predict", body));
    }

    private JsonNode forward(String url, Map<String, Object> body) {
        try {
            String payload = objectMapper.writeValueAsString(body);
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(TIMEOUT)
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(payload))
                    .build();
            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() >= 400) {
                throw new ResponseStatusException(HttpStatus.valueOf(resp.statusCode()),
                        "推理服务返回错误: " + resp.body());
            }
            return objectMapper.readTree(resp.body());
        } catch (ResponseStatusException e) {
            throw e;
        } catch (Exception e) {
            log.warn("Serving proxy predict failed url={} error={}", url, e.getMessage());
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                    "推理服务调用失败: " + e.getMessage());
        }
    }
}
