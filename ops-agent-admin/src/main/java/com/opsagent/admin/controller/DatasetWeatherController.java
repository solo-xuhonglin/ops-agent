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
     * 返回某数据集的天气时间序列，按地区分组，供前端图表使用。
     * 返回结构: { regions: [ "北京", ... ], dates: [ "2024-01-01", ... ], series: { "北京": [ {date, tMax, tMin, tAvg, precip}, ... ] } }
     */
    @GetMapping("/{id}/weather")
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> weather(@PathVariable Long id) {
        List<DatasetWeather> all = weatherRepository.findByDatasetIdOrderByRegionAscDateAsc(id);
        List<String> regions = new ArrayList<>();
        List<String> dates = new ArrayList<>();
        Map<String, List<Map<String, Object>>> series = new LinkedHashMap<>();
        for (DatasetWeather w : all) {
            if (!regions.contains(w.getRegion())) regions.add(w.getRegion());
            String d = w.getDate().toString();
            if (!dates.contains(d)) dates.add(d);
            series.computeIfAbsent(w.getRegion(), k -> new ArrayList<>())
                    .add(Map.of(
                            "date", d,
                            "tMax", w.getTMax(),
                            "tMin", w.getTMin(),
                            "tAvg", w.getTAvg(),
                            "precip", w.getPrecip()
                    ));
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("regions", regions);
        result.put("dates", dates);
        result.put("series", series);
        return ApiResponse.ok(result);
    }
}
