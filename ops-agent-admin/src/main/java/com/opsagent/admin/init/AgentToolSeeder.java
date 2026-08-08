package com.opsagent.admin.init;

import com.opsagent.admin.entity.AgentTool;
import com.opsagent.admin.repository.AgentToolRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * agent_tools 种子数据：admin 现有 REST API 子集（能力=数据，不新开发接口）。
 * 幂等：按 name 缺省插入，已有则不动（schema 变更需手工更新或清表重种）。
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class AgentToolSeeder implements ApplicationRunner {

    private final AgentToolRepository repository;

    private record ToolSpec(String name, String description, String method, String path,
                            String permission, boolean isWrite, String schema) {}

    @Override
    public void run(ApplicationArguments args) {
        for (ToolSpec t : SEED) {
            if (repository.findByName(t.name()).isEmpty()) {
                AgentTool tool = new AgentTool();
                tool.setName(t.name());
                tool.setDescription(t.description());
                tool.setHttpMethod(t.method());
                tool.setPathTemplate(t.path());
                tool.setAuthPermission(t.permission());
                tool.setIsWrite(t.isWrite());
                tool.setParamsSchema(t.schema());
                repository.save(tool);
                log.info("seeded agent tool: {} (write={})", t.name(), t.isWrite());
            }
        }
        log.info("agent_tools seeded: {} tools", repository.count());
    }

    private static final List<ToolSpec> SEED = List.of(
            // ===== 只读工具（透传 taskToken 即可调）=====
            new ToolSpec("dataset.list", "List datasets (paginated, filter by region/status)",
                    "GET", "/api/datasets", "dataset:read", false, SCHEMA_PAGE),
            new ToolSpec("dataset.get", "Get dataset detail by id",
                    "GET", "/api/datasets/{datasetId}", "dataset:read", false,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\",\"description\":\"dataset id\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("dataset.get_file_url", "Get presigned URL of the dataset CSV file",
                    "GET", "/api/datasets/{datasetId}/file/url", "dataset:read", false,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"},\"expiryMinutes\":{\"type\":\"integer\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("model.list", "List model versions (paginated)",
                    "GET", "/api/models", "model:read", false, SCHEMA_PAGE),
            new ToolSpec("model.get", "Get model version detail incl. metrics/hyperparameters",
                    "GET", "/api/models/{modelVersionId}", "model:read", false,
                    "{\"type\":\"object\",\"properties\":{\"modelVersionId\":{\"type\":\"integer\"}},\"required\":[\"modelVersionId\"]}"),
            new ToolSpec("training.list", "List training jobs (paginated)",
                    "GET", "/api/training/jobs", "training:read", false, SCHEMA_PAGE),
            new ToolSpec("training.get", "Get training job detail (status/container/timings)",
                    "GET", "/api/training/jobs/{jobId}", "training:read", false,
                    "{\"type\":\"object\",\"properties\":{\"jobId\":{\"type\":\"integer\"}},\"required\":[\"jobId\"]}"),
            new ToolSpec("training.get_logs_url", "Get presigned URL of training job logs",
                    "GET", "/api/training/jobs/{jobId}/logs", "training:read", false,
                    "{\"type\":\"object\",\"properties\":{\"jobId\":{\"type\":\"integer\"},\"expiryMinutes\":{\"type\":\"integer\"}},\"required\":[\"jobId\"]}"),
            new ToolSpec("serving.list", "List serving endpoints (paginated)",
                    "GET", "/api/serving/endpoints", "serving:read", false, SCHEMA_PAGE),
            new ToolSpec("serving.get", "Get serving endpoint detail",
                    "GET", "/api/serving/endpoints/{endpointId}", "serving:read", false,
                    "{\"type\":\"object\",\"properties\":{\"endpointId\":{\"type\":\"integer\"}},\"required\":[\"endpointId\"]}"),
            new ToolSpec("serving.predict", "Run LSTM inference via a deployed endpoint (read-only semantic)",
                    "POST", "/api/serving-proxy/{endpointId}/predict", "serving:read", false,
                    "{\"type\":\"object\",\"properties\":{\"endpointId\":{\"type\":\"integer\"},\"values\":{\"type\":\"array\",\"items\":{\"type\":\"number\"}},\"horizon\":{\"type\":\"integer\"}},\"required\":[\"endpointId\",\"values\"]}"),

            // ===== 写工具（M3 接 grantKey 授权后由 agent 执行）=====
            new ToolSpec("training.create", "Create a training job (needs human approval)",
                    "POST", "/api/training/jobs", "training:write", true,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"},\"name\":{\"type\":\"string\"},\"version\":{\"type\":\"string\"},\"algorithm\":{\"type\":\"string\"},\"hyperparameters\":{\"type\":\"object\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("training.delete", "Abort/delete a training job (stops container, cleans up)",
                    "DELETE", "/api/training/jobs/{jobId}", "training:write", true,
                    "{\"type\":\"object\",\"properties\":{\"jobId\":{\"type\":\"integer\"}},\"required\":[\"jobId\"]}"),
            new ToolSpec("serving.deploy", "Deploy a READY model version as serving endpoint",
                    "POST", "/api/serving/deploy", "serving:write", true,
                    "{\"type\":\"object\",\"properties\":{\"modelVersionId\":{\"type\":\"integer\"}},\"required\":[\"modelVersionId\"]}"),
            new ToolSpec("serving.undeploy", "Undeploy a serving endpoint (stops container)",
                    "POST", "/api/serving/endpoints/{endpointId}/undeploy", "serving:write", true,
                    "{\"type\":\"object\",\"properties\":{\"endpointId\":{\"type\":\"integer\"}},\"required\":[\"endpointId\"]}"),
            new ToolSpec("dataset.collect_weather", "Trigger Open-Meteo weather collection for a dataset",
                    "GET", "/api/datasets/{datasetId}/weather", "dataset:write", true,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"}},\"required\":[\"datasetId\"]}")
    );

    /** 分页类工具的通用参数 schema */
    private static final String SCHEMA_PAGE =
            "{\"type\":\"object\",\"properties\":{\"page\":{\"type\":\"integer\"},\"size\":{\"type\":\"integer\"}},\"required\":[]}";
}
