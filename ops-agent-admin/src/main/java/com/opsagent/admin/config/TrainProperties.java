package com.opsagent.admin.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConfigurationProperties(prefix = "train")
@Getter
@Setter
public class TrainProperties {

    /** 是否启用训练编排（关闭后点击训练会直接报错，便于无 docker 的开发环境） */
    private boolean enabled = true;

    /** 训练镜像名（由 deploy.sh 通过 compose profile 预构建） */
    private String image = "ops-agent-train:latest";

    /** 训练容器加入的 docker 网络（需与 compose 中 admin/minio 所在网络一致） */
    private String network = "ops-agent-opsnet";

    /** 单次训练最大时长（分钟），超时强杀并标记 FAILED */
    private int timeoutMinutes = 60;
}
