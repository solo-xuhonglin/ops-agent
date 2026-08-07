package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Entity
@Table(name = "dataset_weather",
        uniqueConstraints = @UniqueConstraint(columnNames = {"dataset_id", "region", "weather_time"}),
        indexes = @Index(columnList = "dataset_id"))
@Getter
@Setter
@NoArgsConstructor
public class DatasetWeather {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "dataset_id", nullable = false)
    private Long datasetId;

    @Column(nullable = false, length = 64)
    private String region;

    @Column(name = "weather_time", nullable = false)
    private LocalDateTime time;

    /** 该小时气温 ℃ */
    @Column(name = "temperature")
    private Double temperature;

    /** 该小时降水量 mm */
    @Column(name = "precip")
    private Double precip;

    public DatasetWeather(Long datasetId, String region, LocalDateTime time) {
        this.datasetId = datasetId;
        this.region = region;
        this.time = time;
    }
}
