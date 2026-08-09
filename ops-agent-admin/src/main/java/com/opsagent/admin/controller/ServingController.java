package com.opsagent.admin.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.ServingEndpoint;
import com.opsagent.admin.service.ServingEndpointService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
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
 * serving 端点管理面 + 推理代理，统一收敛在 /api/serving/endpoints 资源路径下：
 *   GET    /api/serving/endpoints                列表（分页/状态/模型筛选）
 *   GET    /api/serving/endpoints/{id}           详情
 *   POST   /api/serving/endpoints/deploy         部署（异步，返回 CREATING）
 *   POST   /api/serving/endpoints/{id}/undeploy  下线（保留记录，置 STOPPED）
 *   POST   /api/serving/endpoints/{id}/predict   推理（代理到 serving 容器）
 *   DELETE /api/serving/endpoints/{id}           物理删除
 */
@RestController
@RequestMapping("/api/serving")
@RequiredArgsConstructor
@Slf4j
public class ServingController {

    private static final Duration TIMEOUT = Duration.ofSeconds(15);

    private final ServingEndpointService servingEndpointService;
    private final ObjectMapper objectMapper;

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    @GetMapping("/endpoints")
    @PreAuthorize("hasAuthority('serving:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size,
                               @RequestParam(required = false) String status,
                               @RequestParam(required = false) Long modelVersionId) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(servingEndpointService.list(pageable, status, modelVersionId));
    }

    @GetMapping("/endpoints/{id}")
    @PreAuthorize("hasAuthority('serving:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(servingEndpointService.get(id));
    }

    /** 部署：校验模型 READY 后由 ServingLauncher 起容器，返回 CREATING 状态的 endpoint */
    @PostMapping("/endpoints/deploy")
    @PreAuthorize("hasAuthority('serving:write')")
    @com.opsagent.admin.service.agent.RequireGrant(action = "serving_deploy", targetType = "serving_endpoint", targetParam = "modelVersionId")
    public ApiResponse<?> deploy(@RequestBody Map<String, Object> body) {
        Object mvId = body.get("modelVersionId");
        if (mvId == null) {
            throw new IllegalArgumentException("缺少 modelVersionId");
        }
        return ApiResponse.ok(servingEndpointService.deploy(Long.valueOf(mvId.toString())));
    }

    /** 下线：停删容器并置 STOPPED（记录保留，供审计/历史查看） */
    @PostMapping("/endpoints/{id}/undeploy")
    @PreAuthorize("hasAuthority('serving:write')")
    @com.opsagent.admin.service.agent.RequireGrant(action = "serving_undeploy", targetType = "serving_endpoint", targetParam = "id")
    public ApiResponse<?> undeploy(@PathVariable Long id) {
        return ApiResponse.ok(servingEndpointService.undeploy(id));
    }

    /** 物理删除 endpoint 记录（先停删容器再删记录，幂等） */
    @DeleteMapping("/endpoints/{id}")
    @PreAuthorize("hasAuthority('serving:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        servingEndpointService.delete(id);
        return ApiResponse.ok();
    }

    /**
     * 推理代理：外部（前端 / agent）统一经本端点调用部署好的 LSTM 模型。
     * 鉴权复用 admin 的 JWT + RBAC（serving:read），serving 容器自身不设鉴权，仅内网可达。
     */
    @PostMapping("/endpoints/{id}/predict")
    @PreAuthorize("hasAuthority('serving:read')")
    public ApiResponse<?> predict(@PathVariable Long id,
                                  @RequestBody Map<String, Object> body) {
        ServingEndpoint ep = servingEndpointService.get(id);
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
            log.warn("Serving predict failed url={} error={}", url, e.getMessage());
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                    "推理服务调用失败: " + e.getMessage());
        }
    }
}
