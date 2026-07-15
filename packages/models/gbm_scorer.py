"""Pure-Python gradient boosted regression trees (single-split *stumps*).

Design goals (§108: candidate model families):

- Provide a real "gradient boosted trees" family (per spec bullet: *Linear factor
  + LightGBM Ranker*, *Rule + LightGBM*, *Ridge + LightGBM* ...) without pulling
  the LightGBM/XGBoost C++ dependency into the runtime. Depth-1 stumps are the
  simplest non-linear learner and are enough to (a) beat linear baselines on
  piecewise / threshold signals and (b) exercise the same pipeline (trainer ->
  ``Model`` protocol -> registry -> promotion gate) as ``TrainedLinearModel``.
- Deterministic. Fixed feature order, deterministic tie-breaking, no RNG.
- Compatible with the ``Model`` protocol in :mod:`packages.models.base` so it
  drops into the existing registry/inference path.

The model is trained via gradient boosting on squared loss:
    pred_0(x) = mean(y)
    for m in 1..M:
        residual_i = y_i - pred_{m-1}(x_i)
        stump_m = best_stump(X, residual)          # minimises MSE
        pred_m  = pred_{m-1} + eta * stump_m
The best stump minimises the sum of within-node squared error over all
(feature, threshold) pairs, where thresholds are midpoints between adjacent
sorted values of that feature.

Small-data friendly. For n rows and p features the fit is O(M * n * p log n).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from packages.common.errors import FeatureMissingError
from packages.common.time_utils import utcnow
from packages.datasets.builder import DatasetRow
from packages.models.base import Prediction
from packages.training.trainer import prepare_matrix


@dataclass(frozen=True, slots=True)
class Stump:
    feature_idx: int
    threshold: float
    left_value: float    # predicted increment when x[feature_idx] <= threshold
    right_value: float


def _best_stump(X: list[list[float]], residuals: list[float]) -> Stump | None:
    """Return the stump minimising SSE on ``residuals``. None if no split helps."""
    n = len(X)
    if n < 2:
        return None
    p = len(X[0])
    total = sum(residuals)
    best: tuple[float, Stump] | None = None
    for f in range(p):
        # Sort rows by feature f. Tie-break by original index for determinism.
        order = sorted(range(n), key=lambda i: (X[i][f], i))
        sorted_vals = [X[i][f] for i in order]
        sorted_res = [residuals[i] for i in order]
        left_sum = 0.0
        # SSE = sum(y^2) - (sum_left)^2/n_left - (sum_right)^2/n_right (constant y^2 term)
        # We minimise -( L^2/nL + R^2/nR ), equivalent.
        for k in range(1, n):
            left_sum += sorted_res[k - 1]
            if sorted_vals[k] == sorted_vals[k - 1]:
                continue  # can't split between equal values
            nL = k
            nR = n - k
            right_sum = total - left_sum
            gain = (left_sum * left_sum) / nL + (right_sum * right_sum) / nR
            thr = 0.5 * (sorted_vals[k - 1] + sorted_vals[k])
            leftv = left_sum / nL
            rightv = right_sum / nR
            cand = Stump(feature_idx=f, threshold=thr,
                         left_value=leftv, right_value=rightv)
            if best is None or gain > best[0]:
                best = (gain, cand)
    return best[1] if best is not None else None


def _score_stump_sequence(x: Sequence[float], base: float,
                          learning_rate: float, stumps: Sequence[Stump]) -> float:
    y = base
    for s in stumps:
        v = s.left_value if x[s.feature_idx] <= s.threshold else s.right_value
        y += learning_rate * v
    return y


@dataclass(frozen=True, slots=True)
class TrainedGBMModel:
    """A frozen, immutable gradient-boosted stump ensemble.

    Predict flow (batch-of-one): read features in declared order, apply each
    stump additively with ``learning_rate``, then squash through a logistic
    to produce a bounded score for the ``Prediction`` contract (same shape as
    ``TrainedLinearModel``, so downstream consumers stay identical).
    """
    model_id: str
    version: str
    feature_names: tuple[str, ...]
    base_score: float
    learning_rate: float
    stumps: tuple[Stump, ...]
    horizon_days: int
    feature_set_hash: str

    def raw_predict(self, features: dict[str, float | None]) -> float:
        vec: list[float] = []
        for n in self.feature_names:
            v = features.get(n)
            if v is None:
                raise FeatureMissingError(
                    f"feature {n} missing at inference"
                )
            vec.append(float(v))
        return _score_stump_sequence(
            vec, self.base_score, self.learning_rate, self.stumps,
        )

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        raw = self.raw_predict(features)
        # Bounded, monotone-in-raw score so the value is safe to consume as a
        # probability-like number even though we are trained on raw returns.
        score = 1.0 / (1.0 + math.exp(-raw))
        return Prediction(
            score=score,
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


class GBMTrainer:
    """Squared-error gradient boosting over depth-1 stumps.

    Parameters mirror LightGBM in spirit (num_rounds, learning_rate) but the
    implementation is intentionally kept trivial. Sufficient for the model
    registry / promotion-gate integration tests demanded by §81.1 and §108.
    """

    def __init__(
        self,
        feature_names: list[str],
        horizon_days: int,
        *,
        num_rounds: int = 50,
        learning_rate: float = 0.1,
        min_samples_split: int = 4,
    ) -> None:
        if num_rounds < 1:
            raise ValueError("num_rounds must be >= 1")
        if not (0.0 < learning_rate <= 1.0):
            raise ValueError("learning_rate must be in (0, 1]")
        self.feature_names = list(feature_names)
        self.horizon_days = horizon_days
        self.num_rounds = num_rounds
        self.learning_rate = learning_rate
        self.min_samples_split = min_samples_split

    def fit(
        self,
        rows: list[DatasetRow],
        *,
        model_id: str,
        version: str | None = None,
    ) -> TrainedGBMModel:
        X, y = prepare_matrix(rows, self.feature_names)
        if len(X) < self.min_samples_split:
            raise FeatureMissingError(
                f"need >= {self.min_samples_split} rows, got {len(X)}"
            )
        base = sum(y) / len(y)
        residuals = [yi - base for yi in y]
        stumps: list[Stump] = []
        for _ in range(self.num_rounds):
            s = _best_stump(X, residuals)
            if s is None:
                break
            # Apply the stump to residuals with the shrinkage learning rate.
            improved = False
            for i, xi in enumerate(X):
                v = s.left_value if xi[s.feature_idx] <= s.threshold else s.right_value
                delta = self.learning_rate * v
                if delta != 0.0:
                    improved = True
                residuals[i] -= delta
            stumps.append(s)
            if not improved:
                break

        feature_set_hash = rows[0].feature_set_hash if rows else ""
        ver = version or hashlib.sha256(
            f"{model_id}|gbm|{feature_set_hash}|{utcnow().isoformat()}"
            .encode()
        ).hexdigest()[:12]
        return TrainedGBMModel(
            model_id=model_id, version=ver,
            feature_names=tuple(self.feature_names),
            base_score=base, learning_rate=self.learning_rate,
            stumps=tuple(stumps),
            horizon_days=self.horizon_days,
            feature_set_hash=feature_set_hash,
        )


__all__ = ["Stump", "TrainedGBMModel", "GBMTrainer"]
