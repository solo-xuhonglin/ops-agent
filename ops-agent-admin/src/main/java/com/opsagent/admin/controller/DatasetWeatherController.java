package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.entity.DatasetWeather;
import com.opsagent.admin.repository.DatasetWeatherRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/datasets")
@RequiredArgsConstructor
public class DatasetWeatherController {

    private final DatasetWeatherRepository weatherRepository;

    /**
     * 返回某数据集的天气时间序列（小时粒度），按地区分组，供前端图表使用。
     * 返回结构: { regions: [ "北京", ... ], times: [ "2024-01-01T00:00", ... ], series: { "北京": [ {time, temperature, precip}, ... ] } }
     */
    @GetMapping("/{id}/weather")
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> weather(@PathVariable Long id) {
        List<DatasetWeather> all = weatherRepository.findByDatasetIdOrderByRegionAscTimeAsc(id);
        List<String> regions = new ArrayList<>();
        List<String> times = new ArrayList<>();
        Map<String, List<Map<String, Object>>> series = new LinkedHashMap<>();
        for (DatasetWeather w : all) {
            if (!regions.contains(w.getRegion())) regions.add(w.getRegion());
            String t = w.getTime().toString();
            if (!times.contains(t)) times.add(t);
            series.computeIfAbsent(w.getRegion(), k -> new ArrayList<>())
                    .add(Map.of(
                            "time", t,
                            "temperature", w.getTemperature(),
                            "precip", w.getPrecip()
                    ));
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("regions", regions);
        result.put("times", times);
        result.put("series", series);
        return ApiResponse.ok(result);
    }
}
