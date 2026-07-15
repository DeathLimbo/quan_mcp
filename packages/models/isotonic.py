"""Isotonic (monotonic) probability calibration.

Purpose (§108): several candidate families need a *calibrated* probability
output — e.g. the CN_FUND_LONG_A family produces a "drawdown probability"
and cannot use raw logistic scores directly because those aren't monotone
w.r.t. empirical frequency after resampling / class-imbalance handling.

Isotonic regression fits the best monotone step function mapping
``raw_score -> empirical_positive_rate`` via the Pool Adjacent Violators
Algorithm (PAVA). It is:
- Non-parametric (no assumption about the shape of the sigmoid).
- Distribution-free.
- Deterministic — no RNG, no gradient descent, tie-broken by original index.

Interface mirrors ``TrainedLinearModel`` / ``TrainedGBMModel`` so the model
can pass through the same registry + gate + prediction path. The calibrator
wraps any inner ``Model`` (its predict_one().score is the raw signal to be
mapped).
"""
from __future__ import annotations

import bisect
import hashlib
from dataclasses import dataclass
from typing import Sequence

from packages.common.errors import FeatureMissingError
from packages.common.time_utils import utcnow
from packages.datasets.builder import DatasetRow
from packages.models.base import Model, Prediction


def _pava(xs: Sequence[float], ys: Sequence[float]
          ) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Pool Adjacent Violators. Returns knot xs' and fitted ys' (monotone up).

    Ties on x are averaged before running PAVA to keep the mapping well-defined.
    Input pairs must be sorted by ``xs`` ascending; caller enforces that.
    """
    # Merge duplicate xs by simple averaging.
    merged_x: list[float] = []
    merged_y: list[float] = []
    weights: list[int] = []
    for xi, yi in zip(xs, ys):
        if merged_x and merged_x[-1] == xi:
            # Combine.
            merged_y[-1] = (merged_y[-1] * weights[-1] + yi) / (weights[-1] + 1)
            weights[-1] += 1
        else:
            merged_x.append(float(xi))
            merged_y.append(float(yi))
            weights.append(1)

    # PAVA: iterate merging violating adjacent blocks.
    blocks_y = list(merged_y)
    blocks_w = list(weights)
    i = 0
    while i < len(blocks_y) - 1:
        if blocks_y[i] > blocks_y[i + 1]:
            new_w = blocks_w[i] + blocks_w[i + 1]
            new_y = (blocks_y[i] * blocks_w[i] +
                     blocks_y[i + 1] * blocks_w[i + 1]) / new_w
            blocks_y[i] = new_y
            blocks_w[i] = new_w
            del blocks_y[i + 1]
            del blocks_w[i + 1]
            # Also collapse the x knot: the block now spans two knots.
            # We keep the *right* endpoint as the block boundary so binary
            # search yields the correct pooled value for lookup >= that knot.
            del merged_x[i]
            if i > 0:
                i -= 1   # re-check previous pair
        else:
            i += 1
    return tuple(merged_x), tuple(blocks_y)


@dataclass(frozen=True, slots=True)
class IsotonicCalibrator:
    """Piecewise-constant monotone map ``raw -> calibrated_probability``.

    Values below the smallest knot map to the first fitted value; values
    at-or-above the k-th knot map to the k-th fitted value. This is the
    standard right-continuous interpretation of the PAVA output.
    """
    knots_x: tuple[float, ...]     # ascending
    values: tuple[float, ...]      # non-decreasing, aligned with knots_x

    def __post_init__(self) -> None:
        if len(self.knots_x) != len(self.values):
            raise ValueError("knots_x and values length mismatch")
        if not self.knots_x:
            raise ValueError("empty calibrator")
        # Cheap non-decreasing check.
        for a, b in zip(self.values, self.values[1:]):
            if a > b:
                raise ValueError("values must be non-decreasing (PAVA output)")

    def map(self, raw: float) -> float:
        # Right-continuous step function: return values[k] where
        # knots_x[k] <= raw < knots_x[k+1].  If raw < knots_x[0], clamp to 0-th.
        i = bisect.bisect_right(self.knots_x, raw) - 1
        if i < 0:
            i = 0
        return float(self.values[i])


@dataclass(frozen=True, slots=True)
class CalibratedModel:
    """A ``Model`` whose score is passed through an isotonic calibrator.

    Feature-set hash / model id are propagated from the inner model unless
    explicitly overridden — governance stays consistent because the
    calibrator only reshapes the score, not the underlying feature
    dependency.
    """
    inner: Model
    calibrator: IsotonicCalibrator
    model_id: str
    version: str
    horizon_days: int
    feature_set_hash: str

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        p = self.inner.predict_one(features)
        calibrated = self.calibrator.map(p.score)
        return Prediction(
            score=calibrated,
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


class IsotonicTrainer:
    """Fit an :class:`IsotonicCalibrator` on ``(inner_score, label)`` pairs.

    Labels are treated as {0, 1} for classifier calibration. The caller may
    also feed continuous labels — the PAVA output remains a valid monotone
    mapping in either case, though the "probability" interpretation only
    holds for 0/1 targets.
    """

    def __init__(self, inner: Model) -> None:
        self.inner = inner

    def fit(
        self,
        rows: list[DatasetRow],
        *,
        model_id: str,
        version: str | None = None,
        horizon_days: int | None = None,
    ) -> CalibratedModel:
        pairs: list[tuple[float, float]] = []
        for r in rows:
            if r.label is None:
                continue
            try:
                s = self.inner.predict_one(r.features).score
            except FeatureMissingError:
                continue
            pairs.append((float(s), float(r.label)))
        if not pairs:
            raise FeatureMissingError(
                "no (score, label) pairs to calibrate on"
            )
        pairs.sort(key=lambda p: p[0])
        xs, ys = zip(*pairs)
        knots, values = _pava(xs, ys)
        cal = IsotonicCalibrator(knots_x=knots, values=values)

        feature_set_hash = rows[0].feature_set_hash if rows else ""
        ver = version or hashlib.sha256(
            f"{model_id}|isotonic|{feature_set_hash}|{utcnow().isoformat()}"
            .encode()
        ).hexdigest()[:12]
        return CalibratedModel(
            inner=self.inner,
            calibrator=cal,
            model_id=model_id,
            version=ver,
            horizon_days=horizon_days if horizon_days is not None
                         else getattr(self.inner, "horizon_days", 0),
            feature_set_hash=feature_set_hash,
        )


__all__ = [
    "IsotonicCalibrator", "CalibratedModel", "IsotonicTrainer",
]
