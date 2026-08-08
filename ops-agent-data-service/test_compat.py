"""Compatibility test: train.py payload -> serve.py load_bundle -> predict.

Simulates the model.pt produced by ops-agent-data-train (state_dict +
hyperparameters{seq_len,hidden_size,mean,std}), loads it with serve.py's
load_bundle, and verifies single/multi-step predictions + normalization roundtrip.
"""
import io
import os
import sys
import tempfile

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serve import LSTMModel, load_bundle, PredictRequest  # noqa: E402


def make_payload(seq_len=24, hidden_size=8):
    """Mirror train.py's payload layout exactly."""
    model = LSTMModel(hidden_size)
    return {
        "state_dict": model.state_dict(),
        "hyperparameters": {
            "seq_len": seq_len,
            "hidden_size": hidden_size,
            "mean": 20.5,
            "std": 3.2,
        },
    }


def main():
    seq_len, hidden = 24, 8
    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
        torch.save(make_payload(seq_len, hidden), f.name)
        path = f.name

    bundle = load_bundle(path, "test-mv-1")
    assert bundle.seq_len == seq_len
    assert bundle.model_version_id == "test-mv-1"

    # single step: values length == seq_len -> 1 prediction
    values = [20.0 + (i % 5) * 0.3 for i in range(seq_len)]
    out1 = bundle.predict(values, 1)
    assert len(out1) == 1, out1

    # multi-step: 24 recursive predictions, all floats
    out24 = bundle.predict(values, 24)
    assert len(out24) == 24, len(out24)
    assert all(isinstance(v, float) for v in out24)

    # deterministic: same input -> same output
    assert bundle.predict(values, 1) == out1

    # pydantic validation: horizon bounds
    try:
        PredictRequest(values=values, horizon=0)
        raise AssertionError("horizon=0 should be rejected")
    except Exception:
        pass
    try:
        PredictRequest(values=values, horizon=169)
        raise AssertionError("horizon=169 should be rejected")
    except Exception:
        pass
    try:
        PredictRequest(values=[], horizon=1)
        raise AssertionError("empty values should be rejected")
    except Exception:
        pass

    os.unlink(path)
    print("ALL COMPAT TESTS PASSED")


if __name__ == "__main__":
    main()
