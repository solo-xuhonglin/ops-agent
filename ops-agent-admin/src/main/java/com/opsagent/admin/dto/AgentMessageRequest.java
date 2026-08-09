package com.opsagent.admin.dto;

import lombok.Data;

@Data
public class AgentMessageRequest {

    /** 用户提问（自然语言）；与 target 二选一，都为空则 422 */
    private String query;

    /** 可选意图提示（question / diagnose_training / ...），默认 question */
    private String taskType;

    /** training_job / serving_endpoint / dataset / model_version（列表页"分析"入口用） */
    private String targetType;

    private Long targetId;

    /** 前端「深度思考」开关（默认 true=thinking 模式；false=fast 非思考） */
    private Boolean reasoning = true;
}
