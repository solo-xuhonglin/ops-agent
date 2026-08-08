#!/usr/bin/env python3
"""ops-agent-data-train: 气象时序预测训练入口。

从 MinIO 下载数据集 CSV（region,time,temperature,precipitation），
用滑窗构造样本训练一个 PyTorch LSTM，预测下一时刻气温，
训练完成后将模型与指标回传 MinIO。

所有配置通过环境变量注入（由 ops-agent-admin 的 TrainingLauncher 注入）。
"""
import os
import io
import csv
import json
import sys
import logging

import boto3
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [train] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("train")


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


def download_csv(client, bucket, key):
    log.info("Downloading dataset %s/%s", bucket, key)
    obj = client.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(data))
    rows = []
    for r in reader:
        rows.append(r)
    log.info("Dataset rows: %d", len(rows))
    return rows


def build_series(rows):
    """按地区聚合气温时间序列（按时间排序）。"""
    by_region = {}
    for r in rows:
        region = r.get("region")
        temp = r.get("temperature")
        if not region or temp in (None, ""):
            continue
        try:
            t = float(temp)
        except ValueError:
            continue
        by_region.setdefault(region, []).append(t)
    for region in by_region:
        by_region[region].sort()
    return by_region


def make_windows(series_map, seq_len):
    """滑窗：用 seq_len 个历史点预测下一个点。"""
    xs, ys = [], []
    for vals in series_map.values():
        if len(vals) <= seq_len:
            continue
        for i in range(len(vals) - seq_len):
            xs.append(vals[i:i + seq_len])
            ys.append(vals[i + seq_len])
    return xs, ys


class TSDataset(Dataset):
    def __init__(self, xs, ys):
        self.xs = torch.tensor(xs, dtype=torch.float32).unsqueeze(-1)
        self.ys = torch.tensor(ys, dtype=torch.float32).unsqueeze(-1)

    def __len__(self):
        return len(self.xs)

    def __getitem__(self, idx):
        return self.xs[idx], self.ys[idx]


class LSTMModel(nn.Module):
    def __init__(self, hidden_size, num_layers=1):
        super().__init__()
        self.lstm = nn.LSTM(input_size=1, hidden_size=hidden_size,
                           num_layers=num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        out = self.fc(out[:, -1, :])
        return out


def main():
    bucket = env("MINIO_BUCKET", "datasets")
    model_bucket = env("MODEL_BUCKET", bucket)
    dataset_key = env("DATASET_OBJECT_KEY")
    model_version_id = env("MODEL_VERSION_ID")
    job_id = env("JOB_ID")
    seq_len = int(env("SEQ_LEN", "24"))
    hidden_size = int(env("HIDDEN_SIZE", "64"))
    epochs = int(env("EPOCHS", "50"))
    batch_size = int(env("BATCH_SIZE", "32"))
    lr = float(env("LR", "0.001"))

    if not dataset_key or not model_version_id:
        raise SystemExit("Missing DATASET_OBJECT_KEY or MODEL_VERSION_ID environment variable")

    log.info("Hyperparameters: seq_len=%d hidden=%d epochs=%d batch=%d lr=%.4f",
             seq_len, hidden_size, epochs, batch_size, lr)

    client = get_client()
    rows = download_csv(client, bucket, dataset_key)
    series_map = build_series(rows)
    if not series_map:
        raise SystemExit("No temperature series parsed from dataset, cannot train")

    xs, ys = make_windows(series_map, seq_len)
    if len(xs) == 0:
        raise SystemExit("Not enough samples (need more than seq_len=%d), cannot train" % seq_len)
    log.info("Built training samples: %d", len(xs))

    # 标准化（基于训练集均值/方差），推理时反向还原
    mean = sum(sum(x) for x in xs) / max(1, sum(len(x) for x in xs))
    var = sum((v - mean) ** 2 for x in xs for v in x) / max(1, sum(len(x) for x in xs))
    std = var ** 0.5 or 1.0
    xs = [[(v - mean) / std for v in x] for x in xs]
    ys = [(v - mean) / std for v in ys]

    dataset = TSDataset(xs, ys)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Using device: %s", device)
    model = LSTMModel(hidden_size).to(device)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    model.train()
    for epoch in range(1, epochs + 1):
        running = 0.0
        n = 0
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            optimizer.step()
            running += loss.item() * xb.size(0)
            n += xb.size(0)
        if epoch % 5 == 0 or epoch == 1:
            log.info("epoch %d/%d  loss=%.6f", epoch, epochs, running / max(1, n))

    # 评估（全量）
    model.eval()
    total_loss = 0.0
    total_mae = 0.0
    total_n = 0
    with torch.no_grad():
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            pred = model(xb)
            total_loss += criterion(pred, yb).item() * xb.size(0)
            total_mae += (pred - yb).abs().mean().item() * xb.size(0)
            total_n += xb.size(0)
    train_loss = total_loss / max(1, total_n)
    mae = total_mae / max(1, total_n)
    rmse = train_loss ** 0.5
    log.info("Evaluation done  train_loss=%.6f  MAE=%.6f  RMSE=%.6f", train_loss, mae, rmse)

    # 保存模型（含归一化参数，便于推理还原）
    model_payload = {
        "state_dict": model.state_dict(),
        "hyperparameters": {
            "seq_len": seq_len,
            "hidden_size": hidden_size,
            "mean": mean,
            "std": std,
        },
    }
    model_key = f"{model_version_id}/model.pt"
    buf = io.BytesIO()
    torch.save(model_payload, buf)
    buf.seek(0)
    client.put_object(Bucket=model_bucket, Key=model_key, Body=buf.getvalue())
    log.info("Model uploaded %s/%s", model_bucket, model_key)

    metrics = {
        "mae": round(float(mae), 6),
        "rmse": round(float(rmse), 6),
        "train_loss": round(float(train_loss), 6),
        "epochs": epochs,
        "hidden_size": hidden_size,
        "seq_len": seq_len,
    }
    metrics_key = f"{model_version_id}/metrics.json"
    client.put_object(Bucket=model_bucket, Key=metrics_key,
                      Body=json.dumps(metrics, ensure_ascii=False).encode("utf-8"))
    log.info("Metrics uploaded %s/%s  %s", model_bucket, metrics_key, json.dumps(metrics))
    log.info("Training job finished jobId=%s", job_id)


if __name__ == "__main__":
    main()
