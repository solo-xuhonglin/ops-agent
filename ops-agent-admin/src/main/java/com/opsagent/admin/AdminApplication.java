package com.opsagent.admin;

import net.devh.boot.grpc.server.autoconfigure.GrpcServerSecurityAutoConfiguration;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

// gRPC 为内网长连接，鉴权在 HTTP 层（scoped taskToken）；排除 grpc starter 的 spring-security
// 自动配置，否则因缺 GrpcAuthenticationReader bean 导致启动失败
@SpringBootApplication(exclude = GrpcServerSecurityAutoConfiguration.class)
@EnableScheduling
public class AdminApplication {

    public static void main(String[] args) {
        SpringApplication.run(AdminApplication.class, args);
    }
}
