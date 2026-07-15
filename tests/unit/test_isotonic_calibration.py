"""Unit tests for isotonic calibration (§108 CN_FUND_LONG_A drawdown-prob head)."""
from __future__ import annotations

import random
from dataclasses import dataclass

from packages.datasets.builder import DatasetRow
from packages.models.base import Prediction
from packages.models.isotonic import (
    CalibratedModel, IsotonicCalibrator, IsotonicTrainer, _pava,
)


def test_pava_on_monotone_input_is_identity():
    xs = (0.1, 0.2, 0.3, 0.4)
    ys = (0.0, 0.25, 0.5, 1.0)
    knots, vals = _pava(xs, ys)
    assert knots == xs
    assert vals == ys


def test_pava_pools_violating_adjacent_blocks():
    # Non-monotone: PAVA should pool the middle two blocks.
    xs = (0.0, 1.0, 2.0, 3.0)
    ys = (0.0, 0.8, 0.4, 1.0)
    knots, vals = _pava(xs, ys)
    # After pooling 0.8 & 0.4 => 0.6.  Result has 3 blocks: 0.0, 0.6, 1.0.
    assert len(vals) == 3
    assert vals[0] == 0.0
    assert abs(vals[1] - 0.6) < 1e-9
    assert vals[2] == 1.0
    assert len(knots) == 3
    for a, b in zip(vals, vals[1:]):
        assert a <= b


def test_calibrator_step_lookup_is_right_continuous():
    cal = IsotonicCalibrator(
        knots_x=(0.0, 1.0, 2.0),
        values=(0.1, 0.5, 0.9),
    )
    assert cal.map(-0.5) == 0.1     # below smallest -> clamp to first
    assert cal.map(0.0)  == 0.1     # at knot
    assert cal.map(0.5)  == 0.1     # between knots
    assert cal.map(1.0)  == 0.5
    assert cal.map(1.99) == 0.5
    assert cal.map(2.0)  == 0.9
    assert cal.map(100)  == 0.9     # above -> last block


def test_calibrator_rejects_non_monotone_values():
    try:
        IsotonicCalibrator(knots_x=(0.0, 1.0), values=(1.0, 0.5))
    except ValueError as e:
        assert "non-decreasing" in str(e)
    else:
        raise AssertionError("expected ValueError")


@dataclass(frozen=True)
class _ScoreOnly:
    """Minimal Model stub whose predict_one returns a preset score."""
    model_id: str = "raw"
    version: str = "v0"
    horizon_days: int = 5

    def predict_one(self, features):
        return Prediction(
            score=float(features["raw"]),
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash="stub",
        )


def _row(raw: float, label: float | None) -> DatasetRow:
    import datetime as _dt
    return DatasetRow(
        as_of_date=_dt.date(2025, 1, 1),
        features={"raw": raw},
        label=label,
        feature_set_hash="stub",
    )


def test_isotonic_trainer_recovers_monotone_probability():
    rng = random.Random(2026)
    rows = []
    # Ground truth: P(label=1) = sigmoid(raw), monotone in raw.
    import math
    for _ in range(400):
        raw = rng.uniform(-3.0, 3.0)
        p = 1.0 / (1.0 + math.exp(-raw))
        label = 1.0 if rng.random() < p else 0.0
        rows.append(_row(raw, label))

    inner = _ScoreOnly()
    calibrated = IsotonicTrainer(inner).fit(
        rows, model_id="cal.fund.dd_prob", horizon_days=60,
    )
    assert isinstance(calibrated, CalibratedModel)
    # Calibrator should be monotone non-decreasing.
    prev = -1.0
    for v in calibrated.calibrator.values:
        assert v >= prev
        prev = v
    # Extremes should be well-separated: low raw -> low prob, high raw -> high.
    low = calibrated.predict_one({"raw": -3.0}).score
    high = calibrated.predict_one({"raw": 3.0}).score
    assert low < 0.25
    assert high > 0.75
    # Every score is a valid probability.
    for raw in (-3.0, -1.0, 0.0, 1.0, 3.0):
        s = calibrated.predict_one({"raw": raw}).score
        assert 0.0 <= s <= 1.0


def test_isotonic_predict_preserves_horizon_and_provenance():
    rows = [_row(-1.0, 0.0), _row(0.0, 0.0), _row(1.0, 1.0), _row(2.0, 1.0)]
    inner = _ScoreOnly(horizon_days=5)
    calibrated = IsotonicTrainer(inner).fit(
        rows, model_id="cal.head.b", horizon_days=20, version="v1",
    )
    p = calibrated.predict_one({"raw": 1.5})
    assert p.model_id == "cal.head.b"
    assert p.model_version == "v1"
    assert p.horizon_days == 20         # overridden, not inner's 5
    assert p.feature_set_hash == "stub"
