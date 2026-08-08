package com.opsagent.admin.service;

import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.repository.ModelVersionRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

@Service
@RequiredArgsConstructor
@Slf4j
public class ModelVersionService {

    private final ModelVersionRepository modelVersionRepository;
    private final Optional<MinioService> minioService;
    private final com.opsagent.admin.config.MinioConfig minioConfig;

    @Transactional(readOnly = true)
    public Page<ModelVersion> list(Pageable pageable) {
        return modelVersionRepository.findAll(pageable);
    }

    @Transactional(readOnly = true)
    public ModelVersion get(Long id) {
        return modelVersionRepository.findById(id)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("模型版本不存在: " + id));
    }

    @Transactional
    public ModelVersion save(ModelVersion mv) {
        return modelVersionRepository.save(mv);
    }

    @Transactional
    public void delete(Long id) {
        ModelVersion mv = modelVersionRepository.findById(id).orElse(null);
        modelVersionRepository.deleteById(id);
        // purge model artifacts (model.pt + metrics.json) so deletion leaves no orphan files
        if (mv != null && mv.getArtifactKey() != null) {
            String artifactKey = mv.getArtifactKey();
            String metricsKey = mv.getId() + "/metrics.json";
            minioService.ifPresent(minio -> {
                try {
                    minio.delete(minioConfig.getModelBucket(), artifactKey);
                } catch (Exception e) {
                    log.warn("MinIO model artifact delete failed mvId={} key={} error={}",
                            id, artifactKey, e.getMessage());
                }
                try {
                    minio.delete(minioConfig.getModelBucket(), metricsKey);
                } catch (Exception e) {
                    log.warn("MinIO model metrics delete failed mvId={} key={} error={}",
                            id, metricsKey, e.getMessage());
                }
            });
        }
    }

    /**
     * 返回模型文件（models 桶内 &lt;id&gt;/model.pt）的预签名下载 URL。
     */
    @Transactional(readOnly = true)
    public String downloadUrl(Long id, int expiryMinutes) {
        ModelVersion mv = get(id);
        if (mv.getArtifactKey() == null) {
            throw new IllegalStateException("模型文件尚未生成（训练可能未完成或失败）");
        }
        MinioService minio = minioService.orElseThrow(
                () -> new IllegalStateException("对象存储未启用，无法生成下载链接"));
        try {
            return minio.presignedUrl(minioConfig.getModelBucket(), mv.getArtifactKey(), expiryMinutes);
        } catch (Exception e) {
            throw new RuntimeException("生成模型下载链接失败: " + e.getMessage(), e);
        }
    }
}
