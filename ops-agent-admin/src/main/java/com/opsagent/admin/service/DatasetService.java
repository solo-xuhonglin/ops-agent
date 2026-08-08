package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.DatasetDto;
import com.opsagent.admin.entity.Dataset;
import com.opsagent.admin.entity.DatasetWeather;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.DatasetRepository;
import com.opsagent.admin.repository.DatasetWeatherRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Service
@RequiredArgsConstructor
@Slf4j
public class DatasetService {

    private final DatasetRepository datasetRepository;
    private final DatasetWeatherRepository weatherRepository;
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
        d.setObjectKey(req.objectKey() != null && !req.objectKey().isBlank()
                ? req.objectKey() : "weather://" + (req.name() == null ? "dataset" : req.name()));
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
        saved.setStatus("READY");
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
        weatherRepository.deleteByDatasetId(id);
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

    @Transactional(readOnly = true)
    public String getObjectKey(Long id) {
        return find(id).getObjectKey();
    }

    private void collectWeather(Dataset d) {
        if (d.getRegions() == null || d.getRegions().isEmpty()
                || d.getDateStart() == null || d.getDateEnd() == null) {
            return;
        }
        try {
            weatherService.collect(d.getId(), d.getRegions(), d.getDateStart(), d.getDateEnd());
        } catch (Exception e) {
            log.warn("天气采集异常 datasetId={} error={}", d.getId(), e.getMessage());
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
