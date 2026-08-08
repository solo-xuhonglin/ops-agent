package com.opsagent.admin.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class AgentTaskRequest {

    /** diagnose_training / diagnose_serving / diagnose_dataset / model_review / question */
    @NotBlank
    private String taskType;

    /** training_job / serving_endpoint / dataset / model_version */
    private String targetType;

    private Long targetId;

    private String query;
}
