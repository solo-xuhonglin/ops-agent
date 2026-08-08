package com.opsagent.admin.dto;

import lombok.Data;

@Data
public class AgentTaskRequest {

    /**
     * 可选意图提示：diagnose_training / diagnose_serving / diagnose_dataset / model_review。
     * 不填默认为 question。agent 决策不依赖它（核心输入是 query + target），仅用于
     * system prompt 轻提示、落库审计与前端筛选。
     */
    private String taskType;

    /** training_job / serving_endpoint / dataset / model_version */
    private String targetType;

    private Long targetId;

    private String query;
}
