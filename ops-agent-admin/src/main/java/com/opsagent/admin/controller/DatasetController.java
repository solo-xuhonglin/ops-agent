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
import org.springframework.web.multipart.MultipartFile;
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

    @DeleteMapping("/{id}")
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> delete(@PathVariable Long id) {
        datasetService.delete(id);
        return ApiResponse.ok();
    }

    @PostMapping("/{id}/file")
    @PreAuthorize("hasAuthority('dataset:write')")
    public ApiResponse<?> uploadFile(@PathVariable Long id, @RequestParam("file") MultipartFile file) {
        MinioService minio = minioService.orElseThrow(
                () -> new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                        "Object storage (MinIO) is not enabled in this environment"));
        try {
            String objectKey = id + "/" + file.getOriginalFilename();
            minio.upload(objectKey, file);
            Long rows = datasetService.countRows(file);
            datasetService.updateObjectKeyAndRowCount(id, objectKey, rows);
            return ApiResponse.ok(java.util.Map.of("objectKey", objectKey, "rowCount", rows));
        } catch (ResourceNotFoundException e) {
            // dataset missing -> propagate as 404 (mapped by global handler)
            throw e;
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "Failed to upload file to object storage: " + e.getMessage());
        }
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
