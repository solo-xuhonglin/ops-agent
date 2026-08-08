# AI Agent（ops-agent-core）：gRPC client 出站拨号 admin，零端口暴露。
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# 依赖走国内 PyPI 镜像（阿里云，兼容性最好；带超时重试防网络抖动）
RUN pip install --no-cache-dir --upgrade pip --timeout 60 --retries 5 && \
    pip install --no-cache-dir -r requirements.txt \
        -i https://mirrors.aliyun.com/pypi/simple \
        --timeout 60 --retries 5

# proto → 生成 gRPC stub（容器内生成，保证与 proto 一致）
COPY proto/agent.proto proto/
COPY gen.sh .
RUN chmod +x gen.sh && ./gen.sh

COPY app/ app/

CMD ["python", "-m", "app.main"]
