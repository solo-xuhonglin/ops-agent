package com.opsagent.admin.dto;

import java.util.Map;

/**
 * 触发训练请求：选择数据集 + 模型元信息 + 超参。
 */
public record TrainingRequest(
        Long datasetId,
        String name,
        String version,
        String algorithm,
        Map<String, Object> hyperparameters) {
}
