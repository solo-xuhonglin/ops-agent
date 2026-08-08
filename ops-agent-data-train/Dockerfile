# 训练镜像：PyTorch CPU 版，开箱即跑，无需 GPU
FROM pytorch/pytorch:2.3.1-cpu

WORKDIR /train

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 训练入口
COPY train.py .

# 所有配置经环境变量注入，由 admin 的 TrainingLauncher 传入
CMD ["python", "train.py"]
