import os

from app.config import Config, backoff_sequence


def test_from_env_defaults():
    cfg = Config.from_env()
    assert cfg.worker_id == "ops-agent-core-1"
    assert cfg.admin_grpc_addr == "localhost:9090"
    assert cfg.reconnect_min_s == 1.0
    assert cfg.reconnect_max_s == 30.0


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "worker-x")
    monkeypatch.setenv("ADMIN_GRPC_ADDR", "admin:9999")
    monkeypatch.setenv("AGENT_RECONNECT_MIN_S", "0.5")
    monkeypatch.setenv("AGENT_RECONNECT_MAX_S", "8.0")
    cfg = Config.from_env()
    assert cfg.worker_id == "worker-x"
    assert cfg.admin_grpc_addr == "admin:9999"
    assert cfg.reconnect_min_s == 0.5
    assert cfg.reconnect_max_s == 8.0
    # 环境变量在测试间隔离
    os.environ.pop("WORKER_ID", None)


def test_backoff_sequence_capped_at_max():
    assert backoff_sequence(1.0, 30.0) == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_backoff_sequence_small_max():
    assert backoff_sequence(1.0, 3.0) == [1.0, 2.0, 3.0]
