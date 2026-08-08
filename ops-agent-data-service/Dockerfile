# 推理镜像：与训练镜像同一套路，基于精简 python 镜像自行安装依赖（CPU 版 torch）。
FROM python:3.11-slim

WORKDIR /serve

COPY requirements.txt .

# 1) CPU 版 torch（官方 CPU 轮子源，约 190MB）
# 2) 其余推理依赖（fastapi/uvicorn/boto3/numpy）走国内 PyPI 镜像
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        torch \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://mirrors.cloud.tencent.com/pypi/simple && \
    pip install --no-cache-dir -r requirements.txt \
        -i https://mirrors.cloud.tencent.com/pypi/simple

COPY serve.py .

# 常驻服务：容器内固定 8000 端口，不映射宿主端口，由 admin 内网转发
EXPOSE 8000
CMD ["python", "serve.py"]
