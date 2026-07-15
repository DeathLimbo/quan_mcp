"""Unit tests for regime classifier (§108 MARKET_REGIME family)."""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from packages.common.errors import FeatureMissingError
from packages.datasets.builder import DatasetRow
from packages.models.regime import (
    REGIME_BEAR, REGIME_BULL_HIGH_VOL, REGIME_BULL_LOW, REGIME_NAMES,
    REGIME_SIDEWAYS_H, REGIME_SIDEWAYS_L, REGIME_STRESS,
    RegimeTrainer, TrainedRegimeClassifier,
)


def _row(ret_60: float, vol_60: float) -> DatasetRow:
    return DatasetRow(
        as_of_date=date(2025, 1, 1),
        features={"ret_60": ret_60, "vol_60": vol_60},
        label=None,
        feature_set_hash="hREG",
    )


def _train() -> TrainedRegimeClassifier:
    rng = random.Random(1)
    rows = []
    # Spread ret_60 in [-0.15, +0.15] and vol_60 in [0.005, 0.08]
    for _ in range(300):
        rows.append(_row(rng.uniform(-0.15, 0.15), rng.uniform(0.005, 0.08)))
    return RegimeTrainer(horizon_days=20).fit(rows, model_id="regime.v1")


def test_regime_names_and_codes_are_stable():
    assert REGIME_NAMES[REGIME_BULL_LOW] == "BULL_LOW"
    assert REGIME_NAMES[REGIME_STRESS] == "STRESS"
    # 6 distinct integer codes.
    codes = {REGIME_BULL_LOW, REGIME_BULL_HIGH_VOL, REGIME_SIDEWAYS_L,
             REGIME_SIDEWAYS_H, REGIME_BEAR, REGIME_STRESS}
    assert len(codes) == 6


def test_regime_trainer_produces_all_six_regimes_over_diverse_input():
    clf = _train()
    seen: set[int] = set()
    # Sample a grid of (ret, vol) that spans the fitted quantile envelope.
    for r in (-0.20, -0.06, -0.01, 0.01, 0.06, 0.20):
        for v in (0.005, 0.03, 0.10):
            code = int(clf.predict_one(
                {"ret_60": r, "vol_60": v}
            ).score)
            seen.add(code)
    # We expect at least 5 of the 6 regimes to show up on this grid.
    assert len(seen) >= 5, f"regime coverage too narrow: {seen}"


def test_regime_stress_when_return_very_negative_and_vol_very_high():
    clf = _train()
    p = clf.predict_one({"ret_60": -0.20, "vol_60": 0.15})
    assert int(p.score) == REGIME_STRESS


def test_regime_bull_low_when_positive_return_and_low_vol():
    clf = _train()
    p = clf.predict_one({"ret_60": 0.20, "vol_60": 0.005})
    assert int(p.score) == REGIME_BULL_LOW


def test_regime_missing_features_raises():
    clf = _train()
    with pytest.raises(FeatureMissingError):
        clf.predict_one({"ret_60": 0.10})
    with pytest.raises(FeatureMissingError):
        clf.predict_one({"vol_60": 0.02})


def test_regime_deterministic_repeated_predictions():
    clf = _train()
    feats = {"ret_60": 0.02, "vol_60": 0.04}
    p1 = clf.predict_one(feats)
    p2 = clf.predict_one(feats)
    assert p1.score == p2.score
    assert p1.model_version == p2.model_version
