from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime

from packages.common.errors import FeatureMissingError
from packages.common.time_utils import utcnow
from packages.datasets.builder import DatasetRow
from packages.models.base import Prediction


def prepare_matrix(rows: list[DatasetRow], feature_names: list[str]) -> tuple[list[list[float]], list[float]]:
    X: list[list[float]] = []
    y: list[float] = []
    for r in rows:
        if r.label is None:
            continue
        vec = [r.features.get(n) for n in feature_names]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vec):
            continue
        X.append([float(v) for v in vec])  # type: ignore[arg-type]
        y.append(float(r.label))
    return X, y


def _solve_normal_equations(X: list[list[float]], y: list[float], lam: float = 1e-6) -> list[float]:
    """Ridge regression via closed form. Pure Python. Small feature counts only."""
    n = len(X)
    if n == 0:
        raise FeatureMissingError("no training rows after dropping NaN labels/features")
    p = len(X[0])
    # X^T X + lam*I
    XtX = [[0.0] * p for _ in range(p)]
    Xty = [0.0] * p
    for i in range(n):
        xi = X[i]
        yi = y[i]
        for a in range(p):
            Xty[a] += xi[a] * yi
            for b in range(p):
                XtX[a][b] += xi[a] * xi[b]
    for a in range(p):
        XtX[a][a] += lam
    # Solve via Gaussian elimination
    aug = [XtX[i] + [Xty[i]] for i in range(p)]
    for col in range(p):
        pivot = max(range(col, p), key=lambda r: abs(aug[r][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pv = aug[col][col]
        if pv == 0:
            raise FeatureMissingError("singular design matrix")
        for j in range(col, p + 1):
            aug[col][j] /= pv
        for r in range(p):
            if r == col:
                continue
            f = aug[r][col]
            for j in range(col, p + 1):
                aug[r][j] -= f * aug[col][j]
    return [aug[i][p] for i in range(p)]


@dataclass(frozen=True, slots=True)
class TrainedLinearModel:
    model_id: str
    version: str
    feature_names: tuple[str, ...]
    weights: tuple[float, ...]
    horizon_days: int
    feature_set_hash: str

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        vec: list[float] = []
        for n in self.feature_names:
            v = features.get(n)
            if v is None:
                raise FeatureMissingError(f"feature {n} missing at inference")
            vec.append(float(v))
        raw = sum(w * x for w, x in zip(self.weights, vec))
        # Squash to [0,1] via logistic for a probability-like score
        score = 1.0 / (1.0 + math.exp(-raw))
        return Prediction(
            score=score,
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


class LinearTrainer:
    """Ridge-regression trainer. Deterministic, no external ML dep."""

    def __init__(self, feature_names: list[str], horizon_days: int, lam: float = 1e-3) -> None:
        self.feature_names = list(feature_names)
        self.horizon_days = horizon_days
        self.lam = lam

    def fit(
        self,
        rows: list[DatasetRow],
        *,
        model_id: str,
        version: str | None = None,
    ) -> TrainedLinearModel:
        X, y = prepare_matrix(rows, self.feature_names)
        w = _solve_normal_equations(X, y, self.lam)
        feature_set_hash = rows[0].feature_set_hash if rows else ""
        ver = version or hashlib.sha256(
            f"{model_id}|{feature_set_hash}|{utcnow().isoformat()}".encode()
        ).hexdigest()[:12]
        return TrainedLinearModel(
            model_id=model_id, version=ver,
            feature_names=tuple(self.feature_names),
            weights=tuple(w),
            horizon_days=self.horizon_days,
            feature_set_hash=feature_set_hash,
        )
