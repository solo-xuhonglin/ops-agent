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
 * 幂等：按 name 缺省插入，已有则不动。
 * 注意：本类只负责"种"，不做任何清理/迁移（保持代码简单）；
 *       历史脏数据（旧工具名、已移除工具）由 scripts/cleanup-agent-data.sql 按需手动清理。
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
            repository.findByName(t.name()).ifPresentOrElse(
                    // 已存在则对齐最新定义（路径/schema/描述可能随接口演进变化）
                    tool -> {
                        tool.setDescription(t.description());
                        tool.setHttpMethod(t.method());
                        tool.setPathTemplate(t.path());
                        tool.setAuthPermission(t.permission());
                        tool.setIsWrite(t.isWrite());
                        tool.setParamsSchema(t.schema());
                        repository.save(tool);
                        log.info("upserted agent tool: {} (write={})", t.name(), t.isWrite());
                    },
                    () -> {
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
                    });
        }
        log.info("agent_tools seeded: {} tools", repository.count());
    }

    /** 分页类工具的通用参数 schema */
    private static final String SCHEMA_PAGE =
            "{\"type\":\"object\",\"properties\":{\"page\":{\"type\":\"integer\"},\"size\":{\"type\":\"integer\"}},\"required\":[]}";

    /** dataset_list：分页 + region/status 筛选 */
    private static final String SCHEMA_DATASET_LIST =
            "{\"type\":\"object\",\"properties\":{\"page\":{\"type\":\"integer\"},\"size\":{\"type\":\"integer\"},\"region\":{\"type\":\"string\"},\"status\":{\"type\":\"string\"}},\"required\":[]}";

    /** model_list / training_list：分页 + status/datasetId 筛选 */
    private static final String SCHEMA_STATUS_DATASET_LIST =
            "{\"type\":\"object\",\"properties\":{\"page\":{\"type\":\"integer\"},\"size\":{\"type\":\"integer\"},\"status\":{\"type\":\"string\"},\"datasetId\":{\"type\":\"integer\"}},\"required\":[]}";

    /** serving_list：分页 + status/modelVersionId 筛选 */
    private static final String SCHEMA_SERVING_LIST =
            "{\"type\":\"object\",\"properties\":{\"page\":{\"type\":\"integer\"},\"size\":{\"type\":\"integer\"},\"status\":{\"type\":\"string\"},\"modelVersionId\":{\"type\":\"integer\"}},\"required\":[]}";

    private static final List<ToolSpec> SEED = List.of(
            // ===== 只读工具（透传 taskToken 即可调）=====
            new ToolSpec("dataset_list", "List datasets (paginated, filter by region/status)",
                    "GET", "/api/datasets", "dataset:read", false, SCHEMA_DATASET_LIST),
            new ToolSpec("dataset_get", "Get dataset detail by id",
                    "GET", "/api/datasets/{datasetId}", "dataset:read", false,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\",\"description\":\"dataset id\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("dataset_get_file_url", "Get presigned URL of the dataset CSV file",
                    "GET", "/api/datasets/{datasetId}/file/url", "dataset:read", false,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"},\"expiryMinutes\":{\"type\":\"integer\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("model_list", "List model versions (paginated, filter by status/datasetId)",
                    "GET", "/api/models", "model:read", false, SCHEMA_STATUS_DATASET_LIST),
            new ToolSpec("model_get", "Get model version detail incl. metrics/hyperparameters",
                    "GET", "/api/models/{modelVersionId}", "model:read", false,
                    "{\"type\":\"object\",\"properties\":{\"modelVersionId\":{\"type\":\"integer\"}},\"required\":[\"modelVersionId\"]}"),
            new ToolSpec("training_list", "List training jobs (paginated, filter by status/datasetId)",
                    "GET", "/api/training/jobs", "training:read", false, SCHEMA_STATUS_DATASET_LIST),
            new ToolSpec("training_get", "Get training job detail (status/container/timings)",
                    "GET", "/api/training/jobs/{jobId}", "training:read", false,
                    "{\"type\":\"object\",\"properties\":{\"jobId\":{\"type\":\"integer\"}},\"required\":[\"jobId\"]}"),
            new ToolSpec("training_get_logs_url", "Get presigned URL of training job logs",
                    "GET", "/api/training/jobs/{jobId}/logs", "training:read", false,
                    "{\"type\":\"object\",\"properties\":{\"jobId\":{\"type\":\"integer\"},\"expiryMinutes\":{\"type\":\"integer\"}},\"required\":[\"jobId\"]}"),
            new ToolSpec("serving_list", "List serving endpoints (paginated, filter by status/modelVersionId)",
                    "GET", "/api/serving/endpoints", "serving:read", false, SCHEMA_SERVING_LIST),
            new ToolSpec("serving_get", "Get serving endpoint detail",
                    "GET", "/api/serving/endpoints/{endpointId}", "serving:read", false,
                    "{\"type\":\"object\",\"properties\":{\"endpointId\":{\"type\":\"integer\"}},\"required\":[\"endpointId\"]}"),
            new ToolSpec("serving_predict", "Run LSTM inference via a deployed endpoint (read-only semantic)",
                    "POST", "/api/serving/endpoints/{endpointId}/predict", "serving:read", false,
                    "{\"type\":\"object\",\"properties\":{\"endpointId\":{\"type\":\"integer\"},\"values\":{\"type\":\"array\",\"items\":{\"type\":\"number\"}},\"horizon\":{\"type\":\"integer\"}},\"required\":[\"endpointId\",\"values\"]}"),

            // ===== 写工具（M3 接 grantKey 授权后由 agent 执行）=====
            new ToolSpec("dataset_create", "Create a dataset and trigger weather collection",
                    "POST", "/api/datasets", "dataset:write", true,
                    "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"description\":{\"type\":\"string\"},\"regions\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"dateStart\":{\"type\":\"string\",\"format\":\"date\"},\"dateEnd\":{\"type\":\"string\",\"format\":\"date\"}},\"required\":[\"name\",\"regions\",\"dateStart\",\"dateEnd\"]}"),
            new ToolSpec("dataset_update", "Update dataset metadata only (name/description/regions/date range), does NOT re-collect",
                    "PUT", "/api/datasets/{datasetId}", "dataset:write", true,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"},\"name\":{\"type\":\"string\"},\"description\":{\"type\":\"string\"},\"regions\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"dateStart\":{\"type\":\"string\",\"format\":\"date\"},\"dateEnd\":{\"type\":\"string\",\"format\":\"date\"},\"status\":{\"type\":\"string\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("dataset_collect", "Explicitly re-collect weather data for a dataset (overwrites CSV by current regions/date range)",
                    "POST", "/api/datasets/{datasetId}/collect", "dataset:write", true,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("dataset_delete", "Delete a dataset and its stored file",
                    "DELETE", "/api/datasets/{datasetId}", "dataset:write", true,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("training_create", "Create a training job (needs human approval)",
                    "POST", "/api/training/jobs", "training:write", true,
                    "{\"type\":\"object\",\"properties\":{\"datasetId\":{\"type\":\"integer\"},\"name\":{\"type\":\"string\"},\"version\":{\"type\":\"string\"},\"algorithm\":{\"type\":\"string\"},\"hyperparameters\":{\"type\":\"object\"}},\"required\":[\"datasetId\"]}"),
            new ToolSpec("training_delete", "Abort/delete a training job (stops container, cleans up)",
                    "DELETE", "/api/training/jobs/{jobId}", "training:write", true,
                    "{\"type\":\"object\",\"properties\":{\"jobId\":{\"type\":\"integer\"}},\"required\":[\"jobId\"]}"),
            new ToolSpec("serving_deploy", "Deploy a READY model version as serving endpoint",
                    "POST", "/api/serving/endpoints/deploy", "serving:write", true,
                    "{\"type\":\"object\",\"properties\":{\"modelVersionId\":{\"type\":\"integer\"}},\"required\":[\"modelVersionId\"]}"),
            new ToolSpec("serving_undeploy", "Undeploy a serving endpoint (stops container)",
                    "POST", "/api/serving/endpoints/{endpointId}/undeploy", "serving:write", true,
                    "{\"type\":\"object\",\"properties\":{\"endpointId\":{\"type\":\"integer\"}},\"required\":[\"endpointId\"]}")
    );
}
