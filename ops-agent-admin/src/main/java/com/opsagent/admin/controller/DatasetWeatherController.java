package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.entity.Dataset;
import com.opsagent.admin.repository.DatasetRepository;
import com.opsagent.admin.service.MinioService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.*;

@RestController
@RequestMapping("/api/datasets")
@RequiredArgsConstructor
public class DatasetWeatherController {

    private final DatasetRepository datasetRepository;
    private final Optional<MinioService> minioService;

    /**
     * 返回某数据集的天气时间序列（小时粒度），按地区分组，供前端图表使用。
     * 数据来源：MinIO 上的 datasets/&lt;id&gt;/weather.csv（采集时写入）。
     * 返回结构: { regions: [...], times: [...], series: { "北京": [ {time, temperature, precip}, ... ] } }
     */
    @GetMapping("/{id}/weather")
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> weather(@PathVariable Long id) {
        Dataset dataset = datasetRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("数据集不存在: " + id));
        String objectKey = dataset.getObjectKey();
        Map<String, Object> empty = Map.of("regions", List.of(), "times", List.of(), "series", Map.of());
        if (objectKey == null || objectKey.startsWith("weather://") || minioService.isEmpty()) {
            return ApiResponse.ok(empty);
        }
        try (InputStream is = minioService.get().download(objectKey);
             BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8))) {
            List<String> regions = new ArrayList<>();
            List<String> times = new ArrayList<>();
            Map<String, List<Map<String, Object>>> series = new LinkedHashMap<>();
            String line;
            boolean header = true;
            while ((line = br.readLine()) != null) {
                if (header) { header = false; continue; }
                if (line.isBlank()) continue;
                String[] c = line.split(",", -1);
                if (c.length < 4) continue;
                String region = c[0];
                String time = c[1];
                String temp = c[2].isBlank() ? null : c[2];
                String precip = c[3].isBlank() ? null : c[3];
                if (!regions.contains(region)) regions.add(region);
                if (!times.contains(time)) times.add(time);
                series.computeIfAbsent(region, k -> new ArrayList<>())
                        .add(Map.of("time", time, "temperature", temp, "precip", precip));
            }
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("regions", regions);
            result.put("times", times);
            result.put("series", series);
            return ApiResponse.ok(result);
        } catch (Exception e) {
            return ApiResponse.ok(empty);
        }
    }
}
