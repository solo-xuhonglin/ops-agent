package com.opsagent.admin.dto;

import jakarta.validation.constraints.NotNull;

import java.util.Map;

/**
 * 触发训练请求：选择数据集 + 模型元信息 + 超参。
 */
public record TrainingRequest(
        @NotNull(message = "数据集不能为空") Long datasetId,
        String name,
        String version,
        String algorithm,
        Map<String, Object> hyperparameters) {
}
