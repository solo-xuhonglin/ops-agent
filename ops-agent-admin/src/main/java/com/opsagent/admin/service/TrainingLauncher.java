package com.opsagent.admin.service;

import com.github.dockerjava.api.DockerClient;
import com.github.dockerjava.core.DefaultDockerClientConfig;
import com.github.dockerjava.core.DockerClientImpl;
import com.github.dockerjava.httpclient5.ApacheDockerHttpClient;
import com.github.dockerjava.api.command.CreateContainerResponse;
import com.github.dockerjava.api.model.HostConfig;
import com.opsagent.admin.config.MinioConfig;
import com.opsagent.admin.config.TrainProperties;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 通过宿主 docker.sock（docker-java）动态拉起训练容器。
 * 一任务一容器：容器名固定为 ops-agent-train-job-<jobId>，跑完由 TrainingJobPoller 回收。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class TrainingLauncher {

    private final TrainProperties trainProperties;
    private final MinioConfig minioConfig;

    private static final String CONTAINER_PREFIX = "ops-agent-train-job-";

    public String containerName(Long jobId) {
        return CONTAINER_PREFIX + jobId;
    }

    /**
     * 起一个训练容器并启动，返回容器 ID（写入 TrainingJob.containerId 供轮询使用）。
     */
    public String launch(Long jobId, Long modelVersionId, String datasetObjectKey,
                         Map<String, Object> hyperparameters) {
        if (!trainProperties.isEnabled()) {
            throw new IllegalStateException("训练编排未启用（train.enabled=false）");
        }
        List<String> env = buildEnv(jobId, modelVersionId, datasetObjectKey, hyperparameters);
        String name = containerName(jobId);

        DockerClient client = buildClient();
        try {
            // 清理同名残留容器（上一轮异常未回收时）
            try {
                client.removeContainerCmd(name).withForce(true).withRemoveVolumes(false).exec();
            } catch (Exception ignore) {
                // 不存在则忽略
            }

            CreateContainerResponse resp = client.createContainerCmd(trainProperties.getImage())
                    .withName(name)
                    .withEnv(env)
                    .withAttachStdout(true)
                    .withAttachStderr(true)
                    .withHostConfig(HostConfig.newHostConfig()
                            .withNetworkMode(trainProperties.getNetwork())
                            .withAutoRemove(false))
                    .exec();
            String containerId = resp.getId();
            client.startContainerCmd(containerId).exec();
            log.info("Training container started jobId={} containerId={} image={}", jobId, containerId, trainProperties.getImage());
            return containerId;
        } finally {
            try {
                client.close();
            } catch (Exception ignore) {
            }
        }
    }

    public void stopAndRemove(Long jobId) {
        String name = containerName(jobId);
        DockerClient client = buildClient();
        try {
            client.removeContainerCmd(name).withForce(true).exec();
        } catch (Exception e) {
            log.warn("Failed to clean up training container jobId={} error={}", jobId, e.getMessage());
        } finally {
            try {
                client.close();
            } catch (Exception ignore) {
            }
        }
    }

    private DockerClient buildClient() {
        DefaultDockerClientConfig config = DefaultDockerClientConfig.createDefaultConfigBuilder().build();
        ApacheDockerHttpClient httpClient = new ApacheDockerHttpClient.Builder()
                .dockerHost(config.getDockerHost())
                .build();
        return DockerClientImpl.getInstance(config, httpClient);
    }

    private List<String> buildEnv(Long jobId, Long modelVersionId, String datasetObjectKey,
                                  Map<String, Object> hyperparameters) {
        Map<String, Object> hp = hyperparameters == null ? Map.of() : hyperparameters;
        List<String> env = new ArrayList<>();
        env.add("MINIO_ENDPOINT=" + minioConfig.getEndpoint());
        env.add("MINIO_ACCESS_KEY=" + minioConfig.getAccessKey());
        env.add("MINIO_SECRET_KEY=" + minioConfig.getSecretKey());
        env.add("MINIO_BUCKET=" + minioConfig.getBucket());
        env.add("MODEL_BUCKET=" + minioConfig.getModelBucket());
        env.add("DATASET_OBJECT_KEY=" + datasetObjectKey);
        env.add("MODEL_VERSION_ID=" + modelVersionId);
        env.add("JOB_ID=" + jobId);
        env.add("SEQ_LEN=" + intOf(hp, "seqLen", 24));
        env.add("HIDDEN_SIZE=" + intOf(hp, "hiddenSize", 64));
        env.add("EPOCHS=" + intOf(hp, "epochs", 50));
        env.add("BATCH_SIZE=" + intOf(hp, "batchSize", 32));
        env.add("LR=" + doubleOf(hp, "lr", 0.001));
        return env;
    }

    private int intOf(Map<String, Object> hp, String key, int def) {
        Object v = hp.get(key);
        if (v == null) return def;
        try {
            return Integer.parseInt(String.valueOf(v));
        } catch (NumberFormatException e) {
            return def;
        }
    }

    private double doubleOf(Map<String, Object> hp, String key, double def) {
        Object v = hp.get(key);
        if (v == null) return def;
        try {
            return Double.parseDouble(String.valueOf(v));
        } catch (NumberFormatException e) {
            return def;
        }
    }
}
