"""Unit tests for the rule-gated hybrid family (§108 ETF long-A / short-C)."""
from __future__ import annotations

from datetime import date
from dataclasses import dataclass

from packages.datasets.builder import DatasetRow
from packages.models.base import Prediction
from packages.models.rule_gated import (
    RuleGatedModel, RuleGatedTrainer, RuleVerdict,
    ma_trend_long_rule, drawdown_short_rule,
)


def _row(feats: dict, label: float | None = None) -> DatasetRow:
    return DatasetRow(
        as_of_date=date(2025, 1, 1),
        features=feats,
        label=label,
        feature_set_hash="hRULE",
    )


def test_ma_trend_long_rule_eligibility():
    # ma5 > ma20, vol OK -> eligible.
    assert ma_trend_long_rule({"ma_5": 11, "ma_20": 10, "vol_20d": 0.02}).eligible
    # ma5 == ma20 -> not eligible (strict >).
    assert not ma_trend_long_rule({"ma_5": 10, "ma_20": 10, "vol_20d": 0.02}).eligible
    # ma5 < ma20 -> not eligible.
    assert not ma_trend_long_rule({"ma_5": 9, "ma_20": 10, "vol_20d": 0.02}).eligible
    # high vol vetoes even when trend up.
    assert not ma_trend_long_rule({"ma_5": 11, "ma_20": 10, "vol_20d": 0.20}).eligible
    # missing features -> not eligible.
    assert not ma_trend_long_rule({"ma_5": None, "ma_20": 10}).eligible


def test_drawdown_short_rule_eligibility():
    assert drawdown_short_rule({"ret_20d": -0.05}).eligible
    assert not drawdown_short_rule({"ret_20d": -0.02}).eligible   # not deep enough
    assert not drawdown_short_rule({"ret_20d": 0.10}).eligible    # up trend
    assert not drawdown_short_rule({"ret_20d": None}).eligible


@dataclass(frozen=True)
class _AlwaysHigh:
    model_id: str = "inner"
    version: str = "v0"
    horizon_days: int = 5
    feature_set_hash: str = "hINNER"

    def predict_one(self, features):
        return Prediction(
            score=0.9,
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


def test_rule_gated_model_veto_returns_veto_score():
    inner = _AlwaysHigh()
    gated = RuleGatedModel(
        rule=ma_trend_long_rule, inner=inner,
        model_id="etf.long_a", version="v1",
        horizon_days=5, feature_set_hash="hCOMB",
    )
    # Rule vetoes -> score = veto_score (default 0), not inner's 0.9.
    p = gated.predict_one({"ma_5": 8, "ma_20": 10, "vol_20d": 0.02})
    assert p.score == 0.0
    assert p.model_id == "etf.long_a"
    assert p.feature_set_hash == "hCOMB"
    # Rule passes -> inner's 0.9 flows through.
    p2 = gated.predict_one({"ma_5": 12, "ma_20": 10, "vol_20d": 0.02})
    assert p2.score == 0.9


def test_rule_gated_trainer_fits_only_on_eligible_rows():
    rows = [
        _row({"ma_5": 12, "ma_20": 10, "vol_20d": 0.02}, label=0.01),  # eligible
        _row({"ma_5": 12, "ma_20": 10, "vol_20d": 0.02}, label=0.02),  # eligible
        _row({"ma_5": 8,  "ma_20": 10, "vol_20d": 0.02}, label=0.05),  # not
        _row({"ma_5": 12, "ma_20": 10, "vol_20d": 0.50}, label=0.05),  # not (vol)
    ]
    captured: list[int] = []

    def factory(eligible_rows):
        captured.append(len(eligible_rows))
        return _AlwaysHigh()

    trainer = RuleGatedTrainer(
        rule=ma_trend_long_rule, inner_factory=factory, horizon_days=5,
    )
    model = trainer.fit(rows, model_id="etf.long_a", version="v1")
    assert isinstance(model, RuleGatedModel)
    assert captured == [2], f"factory saw wrong eligible-row count: {captured}"


def test_rule_gated_short_and_long_rules_disagree_on_middle():
    # Case where long rule would maybe fire but short rule wouldn't, and vice versa.
    long_feats = {"ma_5": 11, "ma_20": 10, "vol_20d": 0.02, "ret_20d": 0.05}
    short_feats = {"ma_5": 8, "ma_20": 10, "vol_20d": 0.02, "ret_20d": -0.10}

    assert ma_trend_long_rule(long_feats).eligible
    assert not ma_trend_long_rule(short_feats).eligible

    assert drawdown_short_rule(short_feats).eligible
    assert not drawdown_short_rule(long_feats).eligible
