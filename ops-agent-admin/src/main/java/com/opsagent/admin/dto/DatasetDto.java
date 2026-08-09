package com.opsagent.admin.dto;

import jakarta.validation.constraints.NotBlank;

import java.time.LocalDate;
import java.util.List;

public class DatasetDto {

    public record Response(
            Long id,
            String name,
            String description,
            String objectKey,
            String region,
            List<String> regions,
            String source,
            String fileFormat,
            Long rowCount,
            LocalDate dateStart,
            LocalDate dateEnd,
            String status,
            Long createdBy) {
    }

    public record CreateRequest(
            @NotBlank(message = "数据集名称不能为空") String name,
            String description,
            String objectKey,
            List<String> regions,
            String source,
            String fileFormat,
            Long rowCount,
            LocalDate dateStart,
            LocalDate dateEnd) {
    }

    public record UpdateRequest(
            @NotBlank(message = "数据集名称不能为空") String name,
            String description,
            List<String> regions,
            String source,
            String fileFormat,
            Long rowCount,
            LocalDate dateStart,
            LocalDate dateEnd,
            String status) {
    }
}
