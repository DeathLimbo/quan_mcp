"""Regime rule-cluster classifier (§108 MARKET_REGIME family).

Spec §108 bullet for MARKET_REGIME: *"Rule clustering; does NOT emit trades
directly."* — this family produces a regime code (BULL_LOW / BULL_HIGH_VOL /
SIDEWAYS_L / SIDEWAYS_H / BEAR / STRESS) that downstream trade-generating
families read as a *feature*, not a trade signal.

Implementation choice: fixed 6-cluster rule-based classifier over
(``ret_60``, ``vol_60``) plus a *tunable* threshold table. We fit the
thresholds on training data by choosing quantile cut-points, then emit the
regime as an integer code. This is enough to (a) exercise the full
train/register pipeline and (b) provide a Model that returns a per-day
regime code that clusters similar-behaving days together.

At inference the model returns a numeric score = regime code (0..5), so
callers can use it as a *feature* for other families. The consuming
family is responsible for mapping the code back to a categorical.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from packages.common.errors import FeatureMissingError
from packages.common.time_utils import utcnow
from packages.datasets.builder import DatasetRow
from packages.models.base import Prediction


# Regime code integers — kept small so they fit inside a Prediction.score
# without ambiguity when quantised.
REGIME_BULL_LOW       = 0
REGIME_BULL_HIGH_VOL  = 1
REGIME_SIDEWAYS_L     = 2
REGIME_SIDEWAYS_H     = 3
REGIME_BEAR           = 4
REGIME_STRESS         = 5

REGIME_NAMES: tuple[str, ...] = (
    "BULL_LOW", "BULL_HIGH_VOL", "SIDEWAYS_L", "SIDEWAYS_H", "BEAR", "STRESS",
)


@dataclass(frozen=True, slots=True)
class RegimeThresholds:
    ret_up: float          # ret_60 > this => trending up
    ret_down: float        # ret_60 < this => trending down
    vol_high: float        # vol_60 > this => high vol
    vol_low: float         # vol_60 < this => low vol
    stress_ret: float      # ret_60 < this AND vol > vol_high => STRESS


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = int(round((len(s) - 1) * q))
    return s[max(0, min(idx, len(s) - 1))]


@dataclass(frozen=True, slots=True)
class TrainedRegimeClassifier:
    thresholds: RegimeThresholds
    model_id: str
    version: str
    horizon_days: int
    feature_set_hash: str

    def _classify(self, ret_60: float, vol_60: float) -> int:
        t = self.thresholds
        if vol_60 > t.vol_high and ret_60 < t.stress_ret:
            return REGIME_STRESS
        if vol_60 > t.vol_high:
            return REGIME_BULL_HIGH_VOL if ret_60 > 0 else REGIME_BEAR
        if ret_60 > t.ret_up:
            return REGIME_BULL_LOW
        if ret_60 < t.ret_down:
            return REGIME_BEAR
        # Sideways: split by vol tier.
        return REGIME_SIDEWAYS_L if vol_60 < t.vol_low else REGIME_SIDEWAYS_H

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        r = features.get("ret_60")
        v = features.get("vol_60")
        if r is None or v is None:
            raise FeatureMissingError(
                "regime classifier needs ret_60 and vol_60"
            )
        code = self._classify(float(r), float(v))
        return Prediction(
            score=float(code),
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


class RegimeTrainer:
    """Fit a :class:`TrainedRegimeClassifier` by choosing quantile cut-points.

    Fits five thresholds from the training rows so no arbitrary constants
    leak from the spec into calibration. The classifier is deterministic
    given the training set (quantile-based, no RNG).
    """

    def __init__(self, horizon_days: int) -> None:
        self.horizon_days = horizon_days

    def fit(
        self,
        rows: list[DatasetRow],
        *,
        model_id: str,
        version: str | None = None,
    ) -> TrainedRegimeClassifier:
        rets: list[float] = []
        vols: list[float] = []
        for r in rows:
            rr = r.features.get("ret_60")
            vv = r.features.get("vol_60")
            if rr is None or vv is None:
                continue
            rets.append(float(rr))
            vols.append(float(vv))
        if not rets:
            raise FeatureMissingError(
                "no rows with (ret_60, vol_60) for regime training"
            )
        thresholds = RegimeThresholds(
            ret_up    = _quantile(rets, 0.6),
            ret_down  = _quantile(rets, 0.4),
            vol_high  = _quantile(vols, 0.7),
            vol_low   = _quantile(vols, 0.3),
            stress_ret= _quantile(rets, 0.1),
        )
        feature_set_hash = rows[0].feature_set_hash if rows else ""
        ver = version or hashlib.sha256(
            f"{model_id}|regime|{feature_set_hash}|{utcnow().isoformat()}"
            .encode()
        ).hexdigest()[:12]
        return TrainedRegimeClassifier(
            thresholds=thresholds,
            model_id=model_id, version=ver,
            horizon_days=self.horizon_days,
            feature_set_hash=feature_set_hash,
        )


__all__ = [
    "RegimeThresholds", "TrainedRegimeClassifier", "RegimeTrainer",
    "REGIME_NAMES",
    "REGIME_BULL_LOW", "REGIME_BULL_HIGH_VOL", "REGIME_SIDEWAYS_L",
    "REGIME_SIDEWAYS_H", "REGIME_BEAR", "REGIME_STRESS",
]
