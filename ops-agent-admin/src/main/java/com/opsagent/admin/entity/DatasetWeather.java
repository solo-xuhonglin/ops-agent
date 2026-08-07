package com.opsagent.admin.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDate;

@Entity
@Table(name = "dataset_weather",
        uniqueConstraints = @UniqueConstraint(columnNames = {"dataset_id", "region", "weather_date"}),
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

    @Column(name = "weather_date", nullable = false)
    private LocalDate date;

    /** 最高气温 ℃ */
    @Column(name = "t_max")
    private Double tMax;

    /** 最低气温 ℃ */
    @Column(name = "t_min")
    private Double tMin;

    /** 平均气温 ℃ */
    @Column(name = "t_avg")
    private Double tAvg;

    /** 降水量 mm */
    @Column(name = "precip")
    private Double precip;

    public DatasetWeather(Long datasetId, String region, LocalDate date) {
        this.datasetId = datasetId;
        this.region = region;
        this.date = date;
    }
}
