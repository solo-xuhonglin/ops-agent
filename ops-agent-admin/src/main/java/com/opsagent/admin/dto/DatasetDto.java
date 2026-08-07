package com.opsagent.admin.dto;

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
            String name,
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
            String name,
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
