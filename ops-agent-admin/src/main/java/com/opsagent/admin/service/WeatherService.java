package com.opsagent.admin.service;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * 通过 Open-Meteo 免费接口（无需 API Key）采集历史天气数据，小时粒度。
 * 文档: https://open-meteo.com/
 * 采集结果拼成 CSV（region,time,temperature,precipitation）上传到 MinIO
 * datasets/&lt;datasetId&gt;/weather.csv，并返回行数。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class WeatherService {

    private static final String BASE = "https://archive-api.open-meteo.com/v1/archive";
    private static final DateTimeFormatter FMT = DateTimeFormatter.ISO_LOCAL_DATE_TIME;
    private static final DateTimeFormatter DATE = DateTimeFormatter.ISO_DATE;

    private final Optional<MinioService> minioService;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final RestTemplate restTemplate = new RestTemplate();

    /**
     * 为某数据集采集指定地区、日期区间的每小时天气，落 MinIO CSV。
     *
     * @return 采集到的数据行数（0 表示未采集/失败）
     */
    public long collect(Long datasetId, List<String> regions, LocalDate start, LocalDate end) {
        if (regions == null || regions.isEmpty() || start == null || end == null) return 0;
        if (minioService.isEmpty()) {
            log.warn("MinIO disabled, skipping weather collection datasetId={}", datasetId);
            return 0;
        }
        List<String[]> rows = new ArrayList<>();
        for (String region : regions) {
            collectRegion(datasetId, region, start, end, rows);
        }
        if (rows.isEmpty()) return 0;

        String csv = toCsv(rows);
        String objectKey = datasetId + "/weather.csv";
        try (ByteArrayInputStream is = new ByteArrayInputStream(csv.getBytes(StandardCharsets.UTF_8))) {
            minioService.get().upload(objectKey, is, csv.getBytes(StandardCharsets.UTF_8).length, "text/csv");
        } catch (Exception e) {
            log.warn("Failed to upload weather CSV to MinIO datasetId={} error={}", datasetId, e.getMessage());
            return 0;
        }
        log.info("Weather collection finished datasetId={} rows={}", datasetId, rows.size());
        return rows.size();
    }

    private void collectRegion(Long datasetId, String region, LocalDate start, LocalDate end, List<String[]> rows) {
        double[] geo = CityGeo.get(region);
        String url = String.format(
                "%s?latitude=%.4f&longitude=%.4f&start_date=%s&end_date=%s"
                        + "&hourly=temperature_2m,precipitation"
                        + "&timezone=Asia/Shanghai",
                BASE, geo[0], geo[1],
                start.format(DATE),
                end.format(DATE));
        try {
            ResponseEntity<String> resp = restTemplate.getForEntity(url, String.class);
            if (!resp.getStatusCode().is2xxSuccessful() || resp.getBody() == null) {
                log.warn("Weather API abnormal response region={} status={}", region, resp.getStatusCode());
                return;
            }
            OpenMeteoResponse data = objectMapper.readValue(resp.getBody(), OpenMeteoResponse.class);
            if (data == null || data.hourly == null || data.hourly.time == null) return;
            int n = data.hourly.time.length;
            for (int i = 0; i < n; i++) {
                LocalDateTime t = LocalDateTime.parse(data.hourly.time[i], FMT);
                String temp = fmt(safe(data.hourly.temperature_2m, i));
                String precip = fmt(safe(data.hourly.precipitation, i));
                rows.add(new String[]{region, t.toString(), temp, precip});
            }
        } catch (Exception e) {
            log.warn("Weather collection failed region={} error={}", region, e.getMessage());
        }
    }

    private String toCsv(List<String[]> rows) {
        StringBuilder sb = new StringBuilder();
        sb.append("region,time,temperature,precipitation\n");
        for (String[] r : rows) {
            sb.append(r[0]).append(',').append(r[1]).append(',')
              .append(r[2]).append(',').append(r[3]).append('\n');
        }
        return sb.toString();
    }

    private String fmt(Double v) {
        return v == null ? "" : String.valueOf(v);
    }

    private Double safe(double[] arr, int i) {
        if (arr == null || i >= arr.length) return null;
        double v = arr[i];
        return Double.isNaN(v) ? null : Math.round(v * 100.0) / 100.0;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private static class OpenMeteoResponse {
        public Hourly hourly;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private static class Hourly {
        public String[] time;
        public double[] temperature_2m;
        public double[] precipitation;
    }
}
