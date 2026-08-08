# 训练镜像：基于精简 python 镜像自行安装依赖（CPU 版 torch）。
# 原 pytorch/pytorch:*-cpu 官方镜像在受限网络下无法通过镜像源拉取，故改为自装。
FROM python:3.11-slim

WORKDIR /train

# 依赖清单（torch 单独从 CPU 源安装，其余走国内 PyPI 镜像）
COPY requirements.txt .

# 1) 安装 CPU 版 torch（来自 pytorch 官方 CPU 轮子源，约 190MB，远小于 CUDA 版）
#    其依赖（typing-extensions 等）从国内 PyPI 镜像补齐
# 2) 安装其余训练依赖（pandas/numpy/boto3/scikit-learn）
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        torch \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://mirrors.cloud.tencent.com/pypi/simple && \
    pip install --no-cache-dir -r requirements.txt \
        -i https://mirrors.cloud.tencent.com/pypi/simple

# 训练入口
COPY train.py .

# 所有配置经环境变量注入，由 admin 的 TrainingLauncher 传入
CMD ["python", "train.py"]
