package com.opsagent.admin.dto;

import java.time.LocalDate;

public class DatasetDto {

    public record Response(
            Long id,
            String name,
            String description,
            String objectKey,
            String region,
            String source,
            String fileFormat,
            Long rowCount,
            LocalDate dateStart,
            LocalDate dateEnd,
            String status,
            Long createdBy) {
    }

    public record CreateRequest(
            String name,
            String description,
            String objectKey,
            String region,
            String source,
            String fileFormat,
            Long rowCount,
            LocalDate dateStart,
            LocalDate dateEnd) {
    }

    public record UpdateRequest(
            String name,
            String description,
            String region,
            String source,
            String fileFormat,
            Long rowCount,
            LocalDate dateStart,
            LocalDate dateEnd,
            String status) {
    }
}
