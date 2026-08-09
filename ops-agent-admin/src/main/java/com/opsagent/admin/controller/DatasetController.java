package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.DatasetDto;
import com.opsagent.admin.service.DatasetService;
import com.opsagent.admin.service.MinioService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.Optional;

@RestController
@RequestMapping("/api/datasets")
@RequiredArgsConstructor
public class DatasetController {

    private final DatasetService datasetService;

    private final Optional<MinioService> minioService;

    @GetMapping
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(datasetService.list(pageable));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> get(@PathVariable Long id) {
        return ApiResponse.ok(datasetService.get(id));
    }

    @PostMapping
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> create(@Valid @RequestBody DatasetDto.CreateRequest req) {
        return ApiResponse.ok(datasetService.create(req));
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> update(@PathVariable Long id, @Valid @RequestBody DatasetDto.UpdateRequest req) {
        return ApiResponse.ok(datasetService.update(id, req));
    }

    /**
     * 显式触发天气数据采集（重新拉取并按当前 regions/日期覆盖 weather.csv）。
     * 与「更新元数据」解耦：PUT 只改元数据，采集由本接口单独完成。
     */
    @PostMapping("/{id}/collect")
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> collect(@PathVariable Long id) {
        return ApiResponse.ok(datasetService.collect(id));
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        datasetService.delete(id);
        return ApiResponse.ok();
    }

    @GetMapping("/{id}/file/url")
    @PreAuthorize("hasAuthority('dataset:read')")
    public ApiResponse<?> fileUrl(@PathVariable Long id, @RequestParam(defaultValue = "30") int expiryMinutes) {
        MinioService minio = minioService.orElseThrow(
                () -> new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                        "Object storage (MinIO) is not enabled in this environment"));
        try {
            String objectKey = datasetService.getObjectKey(id);
            if (objectKey == null) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Dataset has no uploaded file");
            }
            String url = minio.presignedUrl(objectKey, expiryMinutes);
            return ApiResponse.ok(java.util.Map.of("url", url, "objectKey", objectKey));
        } catch (ResponseStatusException e) {
            throw e;
        } catch (ResourceNotFoundException e) {
            // dataset missing -> propagate as 404 (mapped by global handler)
            throw e;
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "Failed to generate file url: " + e.getMessage());
        }
    }
}
