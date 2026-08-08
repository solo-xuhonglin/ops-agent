package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.DatasetDto;
import com.opsagent.admin.entity.Dataset;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.DatasetRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Service
@RequiredArgsConstructor
@Slf4j
public class DatasetService {

    private final DatasetRepository datasetRepository;
    private final CurrentUser currentUser;
    private final UserRepository userRepository;
    private final WeatherService weatherService;
    private final Optional<MinioService> minioService;

    @Transactional(readOnly = true)
    public Page<DatasetDto.Response> list(Pageable pageable) {
        return datasetRepository.findAll(pageable).map(this::toResponse);
    }

    @Transactional(readOnly = true)
    public DatasetDto.Response get(Long id) {
        return toResponse(find(id));
    }

    @Transactional
    public DatasetDto.Response create(DatasetDto.CreateRequest req) {
        Dataset d = new Dataset();
        d.setName(req.name());
        d.setDescription(req.description());
        d.setObjectKey("weather://" + (req.name() == null ? "dataset" : req.name()));
        d.setRegions(req.regions() == null ? new ArrayList<>() : req.regions());
        d.setSource(req.source());
        d.setFileFormat(req.fileFormat());
        d.setRowCount(req.rowCount());
        d.setDateStart(req.dateStart());
        d.setDateEnd(req.dateEnd());
        d.setStatus("COLLECTING");
        d.setCreatedBy(currentUserId());
        Dataset saved = datasetRepository.save(d);
        collectWeather(saved);
        return toResponse(datasetRepository.save(saved));
    }

    @Transactional
    public DatasetDto.Response update(Long id, DatasetDto.UpdateRequest req) {
        Dataset d = find(id);
        d.setName(req.name());
        d.setDescription(req.description());
        d.setRegions(req.regions() == null ? new ArrayList<>() : req.regions());
        d.setSource(req.source());
        d.setFileFormat(req.fileFormat());
        d.setRowCount(req.rowCount());
        d.setDateStart(req.dateStart());
        d.setDateEnd(req.dateEnd());
        d.setStatus(req.status());
        Dataset saved = datasetRepository.save(d);
        collectWeather(saved);
        return toResponse(saved);
    }

    @Transactional
    public void delete(Long id) {
        Dataset d = find(id);
        String objectKey = d.getObjectKey();
        datasetRepository.delete(d);
        // purge the associated MinIO object so deletion leaves no orphan file
        minioService.ifPresent(minio -> {
            if (objectKey != null && !objectKey.startsWith("weather://")) {
                try {
                    minio.delete(objectKey);
                } catch (Exception e) {
                    log.warn("MinIO object delete failed datasetId={} objectKey={} error={}",
                            id, objectKey, e.getMessage());
                }
            }
        });
    }

    @Transactional
    public void updateObjectKey(Long id, String objectKey) {
        Dataset d = find(id);
        d.setObjectKey(objectKey);
        datasetRepository.save(d);
    }

    @Transactional
    public void updateObjectKeyAndRowCount(Long id, String objectKey, Long rowCount) {
        Dataset d = find(id);
        d.setObjectKey(objectKey);
        d.setRowCount(rowCount);
        datasetRepository.save(d);
    }

    @Transactional(readOnly = true)
    public String getObjectKey(Long id) {
        return find(id).getObjectKey();
    }

    /**
     * 上传文件后统计行数（含表头减 1）。失败返回 null，不阻断上传主流程。
     */
    public Long countRows(MultipartFile file) {
        try (BufferedReader br = new BufferedReader(
                new InputStreamReader(file.getInputStream(), StandardCharsets.UTF_8))) {
            long lines = 0;
            while (br.readLine() != null) lines++;
            return Math.max(0, lines - 1);
        } catch (Exception e) {
            log.warn("Failed to count file rows error={}", e.getMessage());
            return null;
        }
    }

    private void collectWeather(Dataset d) {
        if (d.getRegions() == null || d.getRegions().isEmpty()
                || d.getDateStart() == null || d.getDateEnd() == null) {
            return;
        }
        try {
            long rows = weatherService.collect(d.getId(), d.getRegions(), d.getDateStart(), d.getDateEnd());
            d.setObjectKey(d.getId() + "/weather.csv");
            d.setRowCount(rows);
            d.setStatus(rows > 0 ? "READY" : "INVALID");
        } catch (Exception e) {
            log.warn("Weather collection error datasetId={} error={}", d.getId(), e.getMessage());
            d.setStatus("INVALID");
        }
    }

    private Dataset find(Long id) {
        return datasetRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("数据集不存在: " + id));
    }

    private Long currentUserId() {
        String username = currentUser.username();
        if (username == null) return null;
        return userRepository.findByUsername(username).map(User::getId).orElse(null);
    }

    private DatasetDto.Response toResponse(Dataset d) {
        return new DatasetDto.Response(d.getId(), d.getName(), d.getDescription(), d.getObjectKey(),
                d.getRegion(), d.getRegions(), d.getSource(), d.getFileFormat(), d.getRowCount(),
                d.getDateStart(), d.getDateEnd(), d.getStatus(), d.getCreatedBy());
    }
}
