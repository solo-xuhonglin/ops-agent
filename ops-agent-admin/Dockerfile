# 构建阶段
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /app

# 国内 Maven 镜像源（服务器在腾讯云时加速；其他环境可注释）
RUN mkdir -p /root/.m2 && printf '%s\n' \
    '<settings>' \
    '  <mirrors>' \
    '    <mirror>' \
    '      <id>aliyun</id>' \
    '      <name>Aliyun Maven</name>' \
    '      <url>https://maven.aliyun.com/repository/public</url>' \
    '      <mirrorOf>central,*</mirrorOf>' \
    '    </mir>' \
    '  </mirrors>' \
    '</settings>' > /root/.m2/settings.xml

COPY pom.xml .
RUN mvn -q -B dependency:go-offline || true
COPY src ./src
RUN mvn -q -B clean package -DskipTests

# 运行阶段
FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
