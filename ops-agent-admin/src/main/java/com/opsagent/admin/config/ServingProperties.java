package com.opsagent.admin.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConfigurationProperties(prefix = "serving")
@Getter
@Setter
public class ServingProperties {

    /** 是否启用 serving 编排（关闭后点击部署会直接报错，便于无 docker 的开发环境） */
    private boolean enabled = true;

    /** 推理镜像名（由 deploy.sh 通过 compose profile 预构建） */
    private String image = "ops-agent-serving:latest";

    /** 推理容器加入的 docker 网络（需与 compose 中 admin/minio 所在网络一致） */
    private String network = "ops-agent-opsnet";

    /** 部署后就绪轮询超时（秒），超时判定 FAILED 并清理容器 */
    private int readyTimeoutSeconds = 60;

    /** 就绪/探活 HTTP 调用超时（毫秒） */
    private int httpTimeoutMillis = 3000;

    /** 运行期探活间隔（毫秒） */
    private long healthCheckIntervalMillis = 30000;

    /** 连续探活失败多少次后标记 UNHEALTHY */
    private int unhealthyThreshold = 3;
}
