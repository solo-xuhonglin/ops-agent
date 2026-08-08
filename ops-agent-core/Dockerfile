# AI Agent（ops-agent-core）：gRPC client 出站拨号 admin，零端口暴露。
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# 依赖走国内 PyPI 镜像（与 train/serving 模块同套路）
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt \
        -i https://mirrors.cloud.tencent.com/pypi/simple

# proto → 生成 gRPC stub（容器内生成，保证与 proto 一致）
COPY proto/agent.proto proto/
COPY gen.sh .
RUN chmod +x gen.sh && ./gen.sh

COPY app/ app/

CMD ["python", "-m", "app.main"]
