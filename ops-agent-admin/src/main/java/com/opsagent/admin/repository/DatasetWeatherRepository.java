package com.opsagent.admin.repository;

import com.opsagent.admin.entity.DatasetWeather;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface DatasetWeatherRepository extends JpaRepository<DatasetWeather, Long> {

    List<DatasetWeather> findByDatasetIdAndRegionOrderByDateAsc(Long datasetId, String region);

    List<DatasetWeather> findByDatasetIdOrderByRegionAscDateAsc(Long datasetId);

    @Modifying
    @Query("DELETE FROM DatasetWeather w WHERE w.datasetId = :datasetId")
    void deleteByDatasetId(@Param("datasetId") Long datasetId);
}
