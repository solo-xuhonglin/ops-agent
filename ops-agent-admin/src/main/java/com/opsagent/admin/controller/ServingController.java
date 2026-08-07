package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.ServingEndpoint;
import com.opsagent.admin.service.ServingEndpointService;
import lombok.RequiredArgsConstructor;

import java.util.List;
import java.util.Map;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/serving")
@RequiredArgsConstructor
public class ServingController {

    private final ServingEndpointService servingEndpointService;

    @GetMapping("/endpoints")
    @PreAuthorize("hasAuthority('serving:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(servingEndpointService.list(pageable));
    }

    @GetMapping("/endpoints/{id}")
    @PreAuthorize("hasAuthority('serving:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(servingEndpointService.get(id));
    }

    /** 供 Python agent 发现当前已部署的 LSTM 工具清单 */
    @GetMapping("/tools")
    @PreAuthorize("hasAuthority('serving:read')")
    public ApiResponse<?> tools() {
        List<Map<String, Object>> tools = servingEndpointService.list(
                        org.springframework.data.domain.PageRequest.of(0, 100,
                                org.springframework.data.domain.Sort.by("id").descending()))
                .getContent().stream()
                .filter(ep -> "DEPLOYED".equals(ep.getStatus()))
                .map(ep -> {
                    Map<String, Object> t = new java.util.LinkedHashMap<>();
                    t.put("name", "lstm_predict");
                    t.put("description", "基于历史天气的 LSTM 预测");
                    t.put("endpointId", ep.getId());
                    t.put("url", ep.getUrl());
                    return t;
                }).toList();
        return ApiResponse.ok(tools);
    }

    @PostMapping("/deploy")
    @PreAuthorize("hasAuthority('serving:write')")
    public ApiResponse<?> deploy(@RequestBody ServingEndpoint ep) {
        return ApiResponse.ok(servingEndpointService.save(ep));
    }

    @DeleteMapping("/endpoints/{id}")
    @PreAuthorize("hasAuthority('serving:write')")
    public ApiResponse<?> undeploy(@PathVariable Long id) {
        servingEndpointService.delete(id);
        return ApiResponse.ok();
    }
}
