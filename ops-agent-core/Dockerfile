FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# proto → 生成 gRPC stub（容器内生成，保证与 proto 一致）
COPY proto/agent.proto proto/
COPY gen.sh .
RUN chmod +x gen.sh && ./gen.sh

COPY app/ app/

CMD ["python", "-m", "app.main"]
