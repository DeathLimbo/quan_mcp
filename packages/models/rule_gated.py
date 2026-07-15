"""Rule + GBM hybrid scorer (§108: CN_ETF_SHORT_C / US_ETF_LONG_A_OR_SHORT_C).

The ETF families are spec-tagged as ``"Rule + LightGBM"``:
- A hand-crafted rule gates whether the name is even *eligible* on a given
  day (e.g. above 5/20-day moving average, or below drawdown threshold for
  the short-C mirror).
- The GBM then scores the eligible names for magnitude / direction ranking.
- Non-eligible names get a neutral / negative score, so a top-K selection
  never picks a name the rule vetoed.

This is a compositional model: the rule is a pure-function callable and
the GBM is any ``Model``. The compound predictor is a plain ``Model``, so
the same registry + promotion gate applies unchanged.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from packages.common.time_utils import utcnow
from packages.models.base import Model, Prediction


# Rule signature: ``rule(features) -> RuleVerdict``.
@dataclass(frozen=True, slots=True)
class RuleVerdict:
    eligible: bool
    # If not eligible, we still emit a bounded score for consistency —
    # `veto_score` is used verbatim (usually near 0 or a small negative).
    veto_score: float = 0.0


Rule = Callable[[dict[str, float | None]], RuleVerdict]


def ma_trend_long_rule(features: dict[str, float | None]) -> RuleVerdict:
    """Long-A example: only eligible when ma_5 > ma_20 AND vol_20d not extreme."""
    ma5 = features.get("ma_5")
    ma20 = features.get("ma_20")
    vol = features.get("vol_20d")
    if ma5 is None or ma20 is None:
        return RuleVerdict(eligible=False, veto_score=0.0)
    if vol is not None and vol > 0.10:  # too volatile
        return RuleVerdict(eligible=False, veto_score=0.0)
    return RuleVerdict(eligible=(ma5 > ma20), veto_score=0.0)


def drawdown_short_rule(features: dict[str, float | None]) -> RuleVerdict:
    """Short-C example: eligible only when the name has already broken down."""
    ret_20 = features.get("ret_20d")
    if ret_20 is None:
        return RuleVerdict(eligible=False, veto_score=0.0)
    # Eligible for short if past-20d return is materially negative.
    return RuleVerdict(eligible=(ret_20 < -0.03), veto_score=0.0)


@dataclass(frozen=True, slots=True)
class RuleGatedModel:
    """Rule-gated wrapper: rule vetoes -> veto_score, else -> inner.

    The inner model is arbitrary but *must* have already been trained on the
    subset of rows that survives the rule if we want the score distribution
    inside the eligible cohort to be meaningful. The trainer helper below
    enforces that discipline.
    """
    rule: Rule
    inner: Model
    model_id: str
    version: str
    horizon_days: int
    feature_set_hash: str

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        v = self.rule(features)
        if not v.eligible:
            return Prediction(
                score=v.veto_score,
                horizon_days=self.horizon_days,
                model_id=self.model_id,
                model_version=self.version,
                feature_set_hash=self.feature_set_hash,
            )
        inner_pred = self.inner.predict_one(features)
        return Prediction(
            score=inner_pred.score,
            horizon_days=self.horizon_days,
            model_id=self.model_id,
            model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


class RuleGatedTrainer:
    """Fits an inner model on the rule-eligible subset only.

    The caller supplies a rule callable and a fitted-model factory. The
    trainer filters rows by the rule, then delegates. The resulting
    :class:`RuleGatedModel` behaves as one ``Model`` from the outside.
    """

    def __init__(
        self,
        rule: Rule,
        inner_factory: Callable[[list], Model],
        horizon_days: int,
    ) -> None:
        self.rule = rule
        self.inner_factory = inner_factory
        self.horizon_days = horizon_days

    def fit(
        self,
        rows: list,
        *,
        model_id: str,
        version: str | None = None,
    ) -> RuleGatedModel:
        eligible = [r for r in rows if self.rule(r.features).eligible]
        # Even if only a few rows are eligible we still delegate — the
        # inner factory is expected to handle small samples defensively.
        inner = self.inner_factory(eligible)
        feature_set_hash = getattr(
            inner, "feature_set_hash",
            rows[0].feature_set_hash if rows else "",
        )
        ver = version or hashlib.sha256(
            f"{model_id}|rule_gated|{feature_set_hash}|{utcnow().isoformat()}"
            .encode()
        ).hexdigest()[:12]
        return RuleGatedModel(
            rule=self.rule, inner=inner,
            model_id=model_id, version=ver,
            horizon_days=self.horizon_days,
            feature_set_hash=feature_set_hash,
        )


__all__ = [
    "RuleVerdict", "Rule", "ma_trend_long_rule", "drawdown_short_rule",
    "RuleGatedModel", "RuleGatedTrainer",
]
