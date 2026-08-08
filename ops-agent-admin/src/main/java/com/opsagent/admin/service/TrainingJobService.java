package com.opsagent.admin.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.TrainingRequest;
import com.opsagent.admin.entity.Dataset;
import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.entity.TrainingJob;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.DatasetRepository;
import com.opsagent.admin.repository.ModelVersionRepository;
import com.opsagent.admin.repository.TrainingJobRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
@RequiredArgsConstructor
@Slf4j
public class TrainingJobService {

    private final TrainingJobRepository trainingJobRepository;
    private final ModelVersionRepository modelVersionRepository;
    private final DatasetRepository datasetRepository;
    private final UserRepository userRepository;
    private final CurrentUser currentUser;
    private final TrainingLauncher trainingLauncher;
    private final Optional<MinioService> minioService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Transactional(readOnly = true)
    public Page<TrainingJob> list(Pageable pageable) {
        return trainingJobRepository.findAll(pageable);
    }

    @Transactional(readOnly = true)
    public TrainingJob get(Long id) {
        return trainingJobRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("训练任务不存在: " + id));
    }

    @Transactional
    public TrainingJob save(TrainingJob job) {
        return trainingJobRepository.save(job);
    }

    @Transactional
    public void delete(Long id) {
        trainingJobRepository.deleteById(id);
    }

    /**
     * 触发一次训练：校验数据集 → 建 ModelVersion(TRAINING) + TrainingJob(PENDING) → 起容器 → 立即返回。
     */
    @Transactional
    public TrainingJob trigger(TrainingRequest req) {
        Dataset dataset = datasetRepository.findById(req.datasetId())
                .orElseThrow(() -> new ResourceNotFoundException("数据集不存在: " + req.datasetId()));
        String objectKey = dataset.getObjectKey();
        if (objectKey == null || objectKey.startsWith("weather://")) {
            throw new IllegalArgumentException("数据集尚无可用数据文件，请先采集或上传数据");
        }

        ModelVersion mv = new ModelVersion();
        mv.setName(req.name() != null ? req.name() : ("model-" + dataset.getId()));
        mv.setVersion(req.version() != null ? req.version() : "v1");
        mv.setAlgorithm(req.algorithm() != null ? req.algorithm() : "LSTM");
        mv.setDatasetId(dataset.getId());
        try {
            mv.setHyperparameters(objectMapper.writeValueAsString(
                    req.hyperparameters() == null ? Map.of() : req.hyperparameters()));
        } catch (Exception e) {
            mv.setHyperparameters("{}");
        }
        mv.setStatus("TRAINING");
        mv.setTrainedBy(currentUserId());
        ModelVersion savedMv = modelVersionRepository.save(mv);

        TrainingJob job = new TrainingJob();
        job.setModelVersionId(savedMv.getId());
        job.setDatasetId(dataset.getId());
        job.setStatus("PENDING");
        job.setTriggeredBy(currentUserId());
        TrainingJob savedJob = trainingJobRepository.save(job);

        String containerId = trainingLauncher.launch(
                savedJob.getId(), savedMv.getId(), objectKey, req.hyperparameters());
        savedJob.setContainerId(containerId);
        savedJob.setStatus("RUNNING");
        savedJob.setStartedAt(java.time.OffsetDateTime.now());
        return trainingJobRepository.save(savedJob);
    }

    /**
     * 返回训练日志的预签名下载 URL（日志已回传到 artifacts/&lt;jobId&gt;/logs.txt）。
     */
    @Transactional(readOnly = true)
    public String logsUrl(Long jobId, int expiryMinutes) {
        TrainingJob job = get(jobId);
        String logKey = job.getLogKey();
        if (logKey == null) {
            throw new IllegalStateException("训练日志尚未生成（任务可能仍在运行）");
        }
        MinioService minio = minioService.orElseThrow(
                () -> new IllegalStateException("对象存储未启用，无法生成日志链接"));
        try {
            return minio.presignedUrl(logKey, expiryMinutes);
        } catch (Exception e) {
            throw new RuntimeException("生成日志链接失败: " + e.getMessage(), e);
        }
    }

    private Long currentUserId() {
        String username = currentUser.username();
        if (username == null) return null;
        return userRepository.findByUsername(username).map(User::getId).orElse(null);
    }
}
