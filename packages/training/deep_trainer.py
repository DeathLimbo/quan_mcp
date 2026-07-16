"""Deep-learning trainers — spec §13.3 third-tier 时序和深度模型.

Two trainers sharing the :class:`Model` protocol:

- :class:`MLPTrainer` — feed-forward network (Linear→ReLU→Linear→Sigmoid).
  Single-point input, fully compatible with the existing walk-forward /
  inference pipeline. The fairest head-to-head vs LightGBM (both consume
  one feature vector per row).
- :class:`LSTMTrainer` — recurrent network over a lookback window. True
  temporal model; ``predict_one`` maintains an internal deque and only emits
  a Prediction once ``lookback`` feature vectors have accumulated (otherwise
  raises ``FeatureMissingError`` so callers can skip warm-up rows).

torch is imported lazily so the training package stays importable without the
heavy dependency. Spec §13.3: "只有经基准证明深度模型有增量价值时才增加" —
compare OOS IC against LightGBM before adopting.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from packages.common.errors import FeatureMissingError
from packages.common.time_utils import utcnow
from packages.datasets.builder import DatasetRow
from packages.models.base import Prediction
from packages.training.trainer import prepare_matrix


# ---- MLP ------------------------------------------------------------------

class _MLPNet:
    """Lazy torch module — built on first use."""

    def __init__(self, input_dim: int, hidden: int = 64) -> None:
        import torch.nn as nn
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden // 2), nn.ReLU(),
            nn.Linear(hidden // 2, 1), nn.Sigmoid(),
        )

    def __call__(self, x):
        return self.net(x).squeeze(-1)


@dataclass
class TrainedMLPModel:
    model_id: str
    version: str
    feature_names: tuple[str, ...]
    net: Any  # _MLPNet
    horizon_days: int
    feature_set_hash: str

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        import torch
        vec: list[float] = []
        for n in self.feature_names:
            v = features.get(n)
            if v is None:
                raise FeatureMissingError(f"feature {n} missing at inference")
            vec.append(float(v))
        with torch.no_grad():
            score = float(self.net(torch.tensor([vec], dtype=torch.float32))[0])
        return Prediction(
            score=max(0.0, min(1.0, score)),
            horizon_days=self.horizon_days,
            model_id=self.model_id, model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


class MLPTrainer:
    """Feed-forward network trainer (binary classifier)."""

    def __init__(self, feature_names: list[str], horizon_days: int,
                 *, hidden: int = 64, epochs: int = 80, lr: float = 1e-3) -> None:
        self.feature_names = list(feature_names)
        self.horizon_days = horizon_days
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr

    def fit(self, rows: list[DatasetRow], *, model_id: str,
            version: str | None = None) -> TrainedMLPModel:
        import numpy as np
        import torch
        import torch.nn as nn
        X, y = prepare_matrix(rows, self.feature_names)
        y_bin = [1.0 if v > 0 else 0.0 for v in y]
        Xt = torch.tensor(np.array(X, dtype=np.float32))
        yt = torch.tensor(y_bin, dtype=torch.float32)
        net = _MLPNet(len(self.feature_names), self.hidden)
        opt = torch.optim.Adam(net.net.parameters(), lr=self.lr)
        loss_fn = nn.BCELoss()
        net.net.train()
        for _ in range(self.epochs):
            opt.zero_grad()
            pred = net(Xt)
            loss = loss_fn(pred, yt)
            loss.backward()
            opt.step()
        feature_set_hash = rows[0].feature_set_hash if rows else ""
        ver = version or hashlib.sha256(
            f"{model_id}|{feature_set_hash}|{utcnow().isoformat()}".encode()
        ).hexdigest()[:12]
        return TrainedMLPModel(
            model_id=model_id, version=ver,
            feature_names=tuple(self.feature_names),
            net=net, horizon_days=self.horizon_days,
            feature_set_hash=feature_set_hash,
        )


# ---- LSTM -----------------------------------------------------------------

class _LSTMNet:
    def __init__(self, input_dim: int, hidden: int = 32, layers: int = 1) -> None:
        import torch.nn as nn
        self.lstm = nn.LSTM(input_dim, hidden, layers, batch_first=True)
        self.head = nn.Sequential(nn.Linear(hidden, 1), nn.Sigmoid())

    def __call__(self, x):  # x: (batch, seq, input_dim)
        import torch
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


@dataclass
class TrainedLSTMModel:
    """LSTM model with an internal lookback buffer.

    ``predict_one`` accumulates feature vectors; once ``lookback`` are
    buffered it runs the LSTM. Fewer than ``lookback`` raises
    ``FeatureMissingError`` (warm-up). Caller should use one model instance
    per instrument to avoid cross-instrument buffer pollution.
    """
    model_id: str
    version: str
    feature_names: tuple[str, ...]
    net: Any  # _LSTMNet
    horizon_days: int
    feature_set_hash: str
    lookback: int
    _buffer: deque = field(default_factory=deque)

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        import torch
        vec: list[float] = []
        for n in self.feature_names:
            v = features.get(n)
            if v is None:
                raise FeatureMissingError(f"feature {n} missing at inference")
            vec.append(float(v))
        self._buffer.append(vec)
        if len(self._buffer) < self.lookback:
            raise FeatureMissingError(
                f"LSTM warm-up: {len(self._buffer)}/{self.lookback} steps")
        seq = list(self._buffer)
        with torch.no_grad():
            x = torch.tensor([seq], dtype=torch.float32)
            score = float(self.net(x)[0])
        return Prediction(
            score=max(0.0, min(1.0, score)),
            horizon_days=self.horizon_days,
            model_id=self.model_id, model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


class LSTMTrainer:
    """Recurrent network trainer over a sliding lookback window."""

    def __init__(self, feature_names: list[str], horizon_days: int,
                 *, lookback: int = 10, hidden: int = 32, epochs: int = 60,
                 lr: float = 1e-3) -> None:
        self.feature_names = list(feature_names)
        self.horizon_days = horizon_days
        self.lookback = lookback
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr

    def fit(self, rows: list[DatasetRow], *, model_id: str,
            version: str | None = None) -> TrainedLSTMModel:
        import numpy as np
        import torch
        import torch.nn as nn
        X, y = prepare_matrix(rows, self.feature_names)
        y_bin = [1.0 if v > 0 else 0.0 for v in y]
        # build sliding windows
        seqs, labels = [], []
        for i in range(self.lookback, len(X)):
            seqs.append(X[i - self.lookback:i])
            labels.append(y_bin[i])
        if not seqs:
            raise FeatureMissingError(
                f"need >= {self.lookback} rows for LSTM, got {len(X)}")
        Xs = torch.tensor(np.array(seqs, dtype=np.float32))
        ys = torch.tensor(labels, dtype=torch.float32)
        net = _LSTMNet(len(self.feature_names), self.hidden)
        opt = torch.optim.Adam(list(net.lstm.parameters()) + list(net.head.parameters()), lr=self.lr)
        loss_fn = nn.BCELoss()
        net.lstm.train(); net.head.train()
        for _ in range(self.epochs):
            opt.zero_grad()
            pred = net(Xs)
            loss = loss_fn(pred, ys)
            loss.backward()
            opt.step()
        feature_set_hash = rows[0].feature_set_hash if rows else ""
        ver = version or hashlib.sha256(
            f"{model_id}|{feature_set_hash}|{utcnow().isoformat()}".encode()
        ).hexdigest()[:12]
        return TrainedLSTMModel(
            model_id=model_id, version=ver,
            feature_names=tuple(self.feature_names),
            net=net, horizon_days=self.horizon_days,
            feature_set_hash=feature_set_hash, lookback=self.lookback,
        )
