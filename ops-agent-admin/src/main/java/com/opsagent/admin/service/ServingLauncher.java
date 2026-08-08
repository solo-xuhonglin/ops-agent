package com.opsagent.admin.service;

import com.github.dockerjava.api.DockerClient;
import com.github.dockerjava.api.command.CreateContainerResponse;
import com.github.dockerjava.api.model.HostConfig;
import com.github.dockerjava.core.DefaultDockerClientConfig;
import com.github.dockerjava.core.DockerClientImpl;
import com.github.dockerjava.httpclient5.ApacheDockerHttpClient;
import com.opsagent.admin.config.MinioConfig;
import com.opsagent.admin.config.ServingProperties;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;

/**
 * 通过宿主 docker.sock（docker-java）动态拉起/回收 serving 推理容器。
 * 一 endpoint 一常驻容器：容器名固定为 ops-agent-serving-<endpointId>，
 * 只加入 compose 内网、不映射宿主端口；就绪/探活由 ServingHealthPoller 负责。
 * 注意：本类只负责容器生命周期，与 ops-agent-data-service（推理服务）完全解耦。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class ServingLauncher {

    private final ServingProperties servingProperties;
    private final MinioConfig minioConfig;

    private static final String CONTAINER_PREFIX = "ops-agent-serving-";

    public String containerName(Long endpointId) {
        return CONTAINER_PREFIX + endpointId;
    }

    /**
     * 起一个 serving 容器并启动，返回容器 ID。
     */
    public String launch(Long endpointId, Long modelVersionId) {
        if (!servingProperties.isEnabled()) {
            throw new IllegalStateException("serving 编排未启用（serving.enabled=false）");
        }
        String name = containerName(endpointId);
        List<String> env = buildEnv(modelVersionId);

        DockerClient client = buildClient();
        try {
            // 清理同名残留容器（上一轮异常未回收时）
            try {
                client.removeContainerCmd(name).withForce(true).withRemoveVolumes(false).exec();
            } catch (Exception ignore) {
                // 不存在则忽略
            }

            CreateContainerResponse resp = client.createContainerCmd(servingProperties.getImage())
                    .withName(name)
                    .withEnv(env)
                    .withAttachStdout(true)
                    .withAttachStderr(true)
                    .withHostConfig(HostConfig.newHostConfig()
                            .withNetworkMode(servingProperties.getNetwork())
                            .withAutoRemove(false))
                    .exec();
            String containerId = resp.getId();
            client.startContainerCmd(containerId).exec();
            log.info("Serving container started endpointId={} containerId={} image={}",
                    endpointId, containerId, servingProperties.getImage());
            return containerId;
        } finally {
            try {
                client.close();
            } catch (Exception ignore) {
            }
        }
    }

    /**
     * 停并删容器（下线）。容器已不存在时幂等忽略。
     */
    public void stopAndRemove(Long endpointId) {
        String name = containerName(endpointId);
        DockerClient client = buildClient();
        try {
            client.removeContainerCmd(name).withForce(true).exec();
        } catch (Exception e) {
            log.warn("Failed to clean up serving container endpointId={} error={}", endpointId, e.getMessage());
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

    private List<String> buildEnv(Long modelVersionId) {
        List<String> env = new ArrayList<>();
        env.add("MINIO_ENDPOINT=" + minioConfig.getEndpoint());
        env.add("MINIO_ACCESS_KEY=" + minioConfig.getAccessKey());
        env.add("MINIO_SECRET_KEY=" + minioConfig.getSecretKey());
        env.add("MODEL_BUCKET=" + minioConfig.getModelBucket());
        env.add("MODEL_VERSION_ID=" + modelVersionId);
        return env;
    }
}
