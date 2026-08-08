#!/usr/bin/env python3
"""ops-agent-data-service: 模型推理服务入口。

从 MinIO 下载模型产物（models/<mvId>/model.pt，含 state_dict 与归一化参数），
加载 PyTorch LSTM 后常驻运行，对外暴露 /health 与 /predict 两个接口。

本服务是"哑推理服务"：只认环境变量 + HTTP，不感知容器编排/健康检查等流程；
部署编排（起容器、探活、下线）由 ops-agent-admin 独立完成。
所有配置通过环境变量注入（由 admin 的 ServingLauncher 注入）。
"""
import os
import sys
import logging

import boto3
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [serve] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("serve")

MAX_HORIZON = 168  # 单次最多递归预测 168 个时刻（7 天×24h）


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def get_client():
    return boto3.client(
        "s3",
        endpoint_url=env("MINIO_ENDPOINT", "http://minio:9000"),
        aws_access_key_id=env("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=env("MINIO_SECRET_KEY", "minioadmin"),
    )


class LSTMModel(nn.Module):
    """与训练模块 train.py 中的结构保持一致（input_size=1，输出下一时刻）。"""

    def __init__(self, hidden_size, num_layers=1):
        super().__init__()
        self.lstm = nn.LSTM(input_size=1, hidden_size=hidden_size,
                           num_layers=num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        out = self.fc(out[:, -1, :])
        return out


class ModelBundle:
    """已加载的模型 + 归一化参数，推理时负责还原到原始量纲。"""

    def __init__(self, model, seq_len, mean, std, model_version_id):
        self.model = model
        self.seq_len = seq_len
        self.mean = mean
        self.std = std
        self.model_version_id = model_version_id

    def predict(self, values, horizon):
        """单步或多步递归预测，返回长度为 horizon 的原始量纲气温列表（单位 ℃）。"""
        model = self.model
        seq_len = self.seq_len
        mean, std = self.mean, self.std

        window = [(v - mean) / std for v in values[-seq_len:]]
        predictions = []
        model.eval()
        with torch.no_grad():
            for _ in range(horizon):
                x = torch.tensor(window, dtype=torch.float32).unsqueeze(0).unsqueeze(-1)
                pred = model(x).item()
                value = pred * std + mean
                predictions.append(round(float(value), 4))
                # 递归：把预测值归一化后接回窗口尾部（滚雪球）
                window = window[1:] + [(pred - mean) / std]
        return predictions


class PredictRequest(BaseModel):
    values: list[float] = Field(..., description="历史气温序列（单位 ℃），长度需 ≥ seq_len")
    horizon: int = Field(1, ge=1, le=MAX_HORIZON, description="预测未来 N 个时刻，默认 1")

    @field_validator("values")
    @classmethod
    def values_not_empty(cls, v):
        if not v:
            raise ValueError("values must not be empty")
        return v


class PredictResponse(BaseModel):
    predictions: list[float]
    modelVersionId: str


def load_bundle(model_file, model_version_id):
    """从本地文件加载模型 payload（state_dict + hyperparameters），返回 ModelBundle。"""
    payload = torch.load(model_file, map_location="cpu", weights_only=False)
    state_dict = payload.get("state_dict")
    hp = payload.get("hyperparameters", {})
    if not state_dict:
        raise RuntimeError("model payload missing state_dict")

    hidden_size = int(hp.get("hidden_size", 64))
    seq_len = int(hp.get("seq_len", 24))
    mean = float(hp.get("mean", 0.0))
    std = float(hp.get("std", 1.0)) or 1.0

    model = LSTMModel(hidden_size)
    model.load_state_dict(state_dict)
    model.eval()
    log.info("Model loaded mvId=%s seq_len=%d hidden=%d mean=%.4f std=%.4f",
             model_version_id, seq_len, hidden_size, mean, std)
    return ModelBundle(model, seq_len, mean, std, model_version_id)


def main():
    model_bucket = env("MODEL_BUCKET", "models")
    model_version_id = env("MODEL_VERSION_ID")
    if not model_version_id:
        raise SystemExit("Missing MODEL_VERSION_ID environment variable")

    # 本地文件路径优先（便于无 MinIO 环境的单元测试），否则从 MinIO 下载
    model_file = env("MODEL_FILE")
    if model_file:
        log.info("Loading model from local file: %s", model_file)
        bundle = load_bundle(model_file, model_version_id)
    else:
        client = get_client()
        key = f"{model_version_id}/model.pt"
        log.info("Downloading model %s/%s", model_bucket, key)
        data = client.get_object(Bucket=model_bucket, Key=key)["Body"].read()
        tmp = "/tmp/model.pt"
        with open(tmp, "wb") as f:
            f.write(data)
        bundle = load_bundle(tmp, model_version_id)

    app = FastAPI(title="ops-agent-data-service", version="1.0.0")
    app.state.bundle = bundle

    @app.get("/health")
    def health():
        return {"status": "ok", "modelVersionId": model_version_id}

    @app.post("/predict", response_model=PredictResponse)
    def predict(req: PredictRequest):
        bundle = app.state.bundle
        if len(req.values) < bundle.seq_len:
            raise HTTPException(
                status_code=400,
                detail=f"values length {len(req.values)} < seq_len {bundle.seq_len}",
            )
        predictions = bundle.predict(req.values, req.horizon)
        return PredictResponse(predictions=predictions, modelVersionId=model_version_id)

    log.info("Serving on :8000 mvId=%s", model_version_id)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
