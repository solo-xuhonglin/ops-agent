package com.opsagent.admin.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.dockerjava.api.DockerClient;
import com.github.dockerjava.core.DefaultDockerClientConfig;
import com.github.dockerjava.core.DockerClientImpl;
import com.github.dockerjava.httpclient5.ApacheDockerHttpClient;
import com.github.dockerjava.api.command.InspectContainerResponse;
import com.github.dockerjava.api.model.Frame;
import com.github.dockerjava.core.command.LogContainerResultCallback;
import com.opsagent.admin.config.TrainProperties;
import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.entity.TrainingJob;
import com.opsagent.admin.repository.ModelVersionRepository;
import com.opsagent.admin.repository.TrainingJobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * 定时巡检训练容器：读退出码判定成败、回收容器、抓日志回传 MinIO、回填 ModelVersion。
 * 容器保持"哑"，不出网回 admin，所有状态由本类轮询归集。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class TrainingJobPoller {

    private static final List<String> ACTIVE = List.of("PENDING", "RUNNING");
    private static final String MODEL_PREFIX = "models/";
    private static final String ARTIFACT_PREFIX = "artifacts/";

    private final TrainingJobRepository trainingJobRepository;
    private final ModelVersionRepository modelVersionRepository;
    private final Optional<MinioService> minioService;
    private final TrainProperties trainProperties;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Scheduled(fixedDelay = 5000)
    public void poll() {
        if (!trainProperties.isEnabled()) return;
        List<TrainingJob> jobs = trainingJobRepository.findByStatusIn(ACTIVE);
        for (TrainingJob job : jobs) {
            try {
                process(job);
            } catch (Exception e) {
                log.warn("Training job polling error jobId={} error={}", job.getId(), e.getMessage());
            }
        }
    }

    private void process(TrainingJob job) {
        String containerId = job.getContainerId();
        if (containerId == null) {
            // 还没拿到容器 ID（极少见），留给下一轮
            return;
        }

        DockerClient client = buildClient();
        try {
            InspectContainerResponse inspect;
            try {
                inspect = client.inspectContainerCmd(containerId).exec();
            } catch (Exception e) {
                // 容器已被外部移除：若已经写过日志则视为已终态，否则判失败
                if (job.getLogKey() == null) {
                    finalizeFailed(job, "Container missing (possibly removed externally)");
                }
                return;
            }

            if (Boolean.TRUE.equals(inspect.getState().getRunning())) {
                job.setStatus("RUNNING");
                // 超时强杀
                if (job.getCreatedAt() != null
                        && job.getCreatedAt().plusMinutes(trainProperties.getTimeoutMinutes()).isBefore(OffsetDateTime.now())) {
                    try {
                        client.stopContainerCmd(containerId).exec();
                    } catch (Exception ignore) {
                    }
                    collectLogsAndRemove(client, job);
                    finalizeFailed(job, "Training timeout (> " + trainProperties.getTimeoutMinutes() + " min), force killed");
                } else {
                    trainingJobRepository.save(job);
                }
                return;
            }

            // 已退出
            Long exitCode = inspect.getState().getExitCodeLong();
            collectLogsAndRemove(client, job);
            if (exitCode != null && exitCode == 0) {
                finalizeSucceeded(job);
            } else {
                finalizeFailed(job, "Training container exit code=" + exitCode);
            }
        } finally {
            try {
                client.close();
            } catch (Exception ignore) {
            }
        }
    }

    private void finalizeSucceeded(TrainingJob job) {
        ModelVersion mv = modelVersionRepository.findById(job.getModelVersionId()).orElse(null);
        if (mv != null) {
            String metricsKey = MODEL_PREFIX + mv.getId() + "/metrics.json";
            minioService.ifPresent(minio -> {
                try (java.io.InputStream is = minio.download(metricsKey)) {
                    String metricsJson = new String(is.readAllBytes(), StandardCharsets.UTF_8);
                    mv.setMetrics(metricsJson);
                } catch (Exception e) {
                    log.warn("Failed to read training metrics mvId={} error={}", mv.getId(), e.getMessage());
                }
            });
            mv.setArtifactKey(MODEL_PREFIX + mv.getId() + "/model.pt");
            mv.setStatus("READY");
            modelVersionRepository.save(mv);
        }
        job.setStatus("SUCCEEDED");
        job.setFinishedAt(OffsetDateTime.now());
        trainingJobRepository.save(job);
        log.info("Training job succeeded jobId={} modelVersionId={}", job.getId(), job.getModelVersionId());
    }

    private void finalizeFailed(TrainingJob job, String reason) {
        ModelVersion mv = modelVersionRepository.findById(job.getModelVersionId()).orElse(null);
        if (mv != null) {
            mv.setStatus("FAILED");
            modelVersionRepository.save(mv);
        }
        job.setStatus("FAILED");
        job.setFinishedAt(OffsetDateTime.now());
        // 若还没日志，写一条原因说明，便于前端查看
        if (job.getLogKey() == null) {
            minioService.ifPresent(minio -> {
                try {
                    String key = ARTIFACT_PREFIX + job.getId() + "/logs.txt";
                    minio.upload(key,
                            new ByteArrayInputStream(reason.getBytes(StandardCharsets.UTF_8)),
                            reason.getBytes(StandardCharsets.UTF_8).length, "text/plain");
                    job.setLogKey(key);
                } catch (Exception e) {
                    log.warn("Failed to write failure-reason log jobId={} error={}", job.getId(), e.getMessage());
                }
            });
        }
        trainingJobRepository.save(job);
        log.warn("Training job failed jobId={} reason={}", job.getId(), reason);
    }

    private void collectLogsAndRemove(DockerClient client, TrainingJob job) {
        StringBuilder sb = new StringBuilder();
        try {
            client.logContainerCmd(job.getContainerId())
                    .withStdOut(true)
                    .withStdErr(true)
                    .exec(new LogContainerResultCallback() {
                        @Override
                        public void onNext(Frame frame) {
                            sb.append(new String(frame.getPayload(), StandardCharsets.UTF_8));
                            super.onNext(frame);
                        }
                    }).awaitCompletion();
        } catch (Exception e) {
            log.warn("Failed to capture training logs jobId={} error={}", job.getId(), e.getMessage());
        }
        String key = ARTIFACT_PREFIX + job.getId() + "/logs.txt";
        final String logText = sb.length() > 0 ? sb.toString() : "(no log output)";
        minioService.ifPresent(minio -> {
            try {
                minio.upload(key,
                        new ByteArrayInputStream(logText.getBytes(StandardCharsets.UTF_8)),
                        logText.getBytes(StandardCharsets.UTF_8).length, "text/plain");
                job.setLogKey(key);
            } catch (Exception e) {
                log.warn("Failed to upload training logs jobId={} error={}", job.getId(), e.getMessage());
            }
        });
        try {
            client.removeContainerCmd(job.getContainerId()).withForce(true).exec();
        } catch (Exception e) {
            log.warn("Failed to remove training container jobId={} error={}", job.getId(), e.getMessage());
        }
    }

    private DockerClient buildClient() {
        DefaultDockerClientConfig config = DefaultDockerClientConfig.createDefaultConfigBuilder().build();
        ApacheDockerHttpClient httpClient = new ApacheDockerHttpClient.Builder()
                .dockerHost(config.getDockerHost())
                .build();
        return DockerClientImpl.getInstance(config, httpClient);
    }
}
