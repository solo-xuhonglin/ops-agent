# 后端：依赖与打包在宿主机完成（mvn clean package → build/*.jar）
# 本镜像仅拷贝已构建好的 jar，构建秒级、依赖可缓存于项目内 .m2
FROM eclipse-temurin:17-jre
WORKDIR /app
COPY build/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
