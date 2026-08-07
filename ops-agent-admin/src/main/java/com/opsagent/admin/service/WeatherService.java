package com.opsagent.admin.service;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.entity.DatasetWeather;
import com.opsagent.admin.repository.DatasetWeatherRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;

/**
 * 通过 Open-Meteo 免费接口（无需 API Key）采集历史天气数据。
 * 文档: https://open-meteo.com/
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class WeatherService {

    private static final String BASE = "https://archive-api.open-meteo.com/v1/archive";
    private final DatasetWeatherRepository weatherRepository;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final RestTemplate restTemplate = new RestTemplate();

    /**
     * 为某数据集采集指定地区、日期区间的每日天气并落库。
     */
    public void collect(Long datasetId, List<String> regions, LocalDate start, LocalDate end) {
        if (regions == null || regions.isEmpty() || start == null || end == null) return;
        weatherRepository.deleteByDatasetId(datasetId);
        for (String region : regions) {
            collectRegion(datasetId, region, start, end);
        }
    }

    private void collectRegion(Long datasetId, String region, LocalDate start, LocalDate end) {
        double[] geo = CityGeo.get(region);
        String url = String.format(
                "%s?latitude=%.4f&longitude=%.4f&start_date=%s&end_date=%s"
                        + "&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum"
                        + "&timezone=Asia%%2FShanghai",
                BASE, geo[0], geo[1],
                start.format(DateTimeFormatter.ISO_DATE),
                end.format(DateTimeFormatter.ISO_DATE));
        try {
            ResponseEntity<String> resp = restTemplate.getForEntity(url, String.class);
            if (!resp.getStatusCode().is2xxSuccessful() || resp.getBody() == null) {
                log.warn("天气接口返回异常 region={} status={}", region, resp.getStatusCode());
                return;
            }
            OpenMeteoResponse data = objectMapper.readValue(resp.getBody(), OpenMeteoResponse.class);
            if (data == null || data.daily == null || data.daily.time == null) return;
            int n = data.daily.time.length;
            List<DatasetWeather> batch = new ArrayList<>(n);
            for (int i = 0; i < n; i++) {
                DatasetWeather w = new DatasetWeather(datasetId, region, LocalDate.parse(data.daily.time[i]));
                w.setTMax(safe(data.daily.temperature_2m_max, i));
                w.setTMin(safe(data.daily.temperature_2m_min, i));
                w.setTAvg(safe(data.daily.temperature_2m_mean, i));
                w.setPrecip(safe(data.daily.precipitation_sum, i));
                batch.add(w);
            }
            weatherRepository.saveAll(batch);
        } catch (Exception e) {
            log.warn("天气采集失败 region={} error={}", region, e.getMessage());
        }
    }

    private Double safe(double[] arr, int i) {
        if (arr == null || i >= arr.length) return null;
        double v = arr[i];
        return Double.isNaN(v) ? null : Math.round(v * 100.0) / 100.0;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private static class OpenMeteoResponse {
        public Daily daily;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private static class Daily {
        public String[] time;
        public double[] temperature_2m_max;
        public double[] temperature_2m_min;
        public double[] temperature_2m_mean;
        public double[] precipitation_sum;
    }
}
