# AI Agent（ops-agent-core）：gRPC client 出站拨号 admin，零端口暴露。
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# 依赖走国内 PyPI 镜像（清华 tuna：simple 文件链接自包含重写，不落到国外 pythonhosted）
RUN pip install --no-cache-dir -r requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --timeout 60 --retries 5

# proto → 生成 gRPC stub（放最后：覆盖本地副本，保证与 proto 一致）
COPY app/ app/
COPY proto/agent.proto proto/
COPY gen.sh .
RUN chmod +x gen.sh && ./gen.sh

CMD ["python", "-m", "app.main"]
