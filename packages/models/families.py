"""Six model-family stubs + baselines per spec §108.

Each family is defined once (id, market, task, horizons, baseline models,
governance metadata) so training pipelines and the model registry can look
them up by identifier without hardcoding strings across the codebase.

Baselines (§81.1) are always trained alongside candidates so a candidate must
beat them on IC/net-return before promotion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

from packages.common.instrument_id import InstrumentId, Market
from packages.data_sources.contracts import Bar
from packages.models.base import Model, Prediction


# ---------------------------------------------------------------------------
# Family catalogue


class FamilyId(str, Enum):
    CN_FUND_LONG_A = "CN_FUND_LONG_A"
    CN_ETF_SHORT_C = "CN_ETF_SHORT_C"
    CN_EQUITY_CROSS_SECTION_B = "CN_EQUITY_CROSS_SECTION_B"
    US_EQUITY_CROSS_SECTION_B = "US_EQUITY_CROSS_SECTION_B"
    US_ETF_LONG_A_OR_SHORT_C = "US_ETF_LONG_A_OR_SHORT_C"
    MARKET_REGIME = "MARKET_REGIME"


@dataclass(frozen=True, slots=True)
class FamilySpec:
    id: FamilyId
    market: Market
    task: str
    horizons_days: tuple[int, ...]
    baselines: tuple[str, ...]
    trade_generating: bool     # False for MARKET_REGIME
    v1_algo_notes: str
    trainer_symbols: tuple[str, ...] = ()   # dotted paths, e.g. "packages.training.trainer.LinearTrainer"


def _resolve(sym: str) -> object:
    """Resolve a dotted symbol path lazily. Raises ImportError on typos."""
    mod, _, name = sym.rpartition(".")
    import importlib
    return getattr(importlib.import_module(mod), name)


FAMILIES: dict[FamilyId, FamilySpec] = {
    FamilyId.CN_FUND_LONG_A: FamilySpec(
        id=FamilyId.CN_FUND_LONG_A,
        market=Market.CN,
        task="long-term quality score + 60/120d return + drawdown probability + DCA multiplier",
        horizons_days=(60, 120),
        baselines=("BuyAndHold", "FixedDCA"),
        trade_generating=True,
        v1_algo_notes="Rule score + Ridge/ElasticNet + LightGBM + Isotonic calibration",
        trainer_symbols=(
            "packages.training.trainer.LinearTrainer",
            "packages.models.gbm_scorer.GBMTrainer",
            "packages.models.isotonic.IsotonicTrainer",
        ),
    ),
    FamilyId.CN_ETF_SHORT_C: FamilySpec(
        id=FamilyId.CN_ETF_SHORT_C,
        market=Market.CN,
        task="5d direction / return / max drawdown",
        horizons_days=(5,),
        baselines=("MovingAverage_5_20", "BuyAndHold"),
        trade_generating=True,
        v1_algo_notes="Logistic + LightGBM ensemble",
        trainer_symbols=(
            "packages.training.trainer.LinearTrainer",
            "packages.models.gbm_scorer.GBMTrainer",
            "packages.models.rule_gated.RuleGatedTrainer",
        ),
    ),
    FamilyId.CN_EQUITY_CROSS_SECTION_B: FamilySpec(
        id=FamilyId.CN_EQUITY_CROSS_SECTION_B,
        market=Market.CN,
        task="5/20d cross-sectional ranking (sector/mcap neutralized)",
        horizons_days=(5, 20),
        baselines=("CrossSectionMomentum_20", "BuyAndHold"),
        trade_generating=True,
        v1_algo_notes="Linear factor + LightGBM Ranker",
        trainer_symbols=(
            "packages.training.trainer.LinearTrainer",
            "packages.models.ranker.RankerTrainer",
        ),
    ),
    FamilyId.US_EQUITY_CROSS_SECTION_B: FamilySpec(
        id=FamilyId.US_EQUITY_CROSS_SECTION_B,
        market=Market.US,
        task="5/20d cross-sectional ranking (independent US training)",
        horizons_days=(5, 20),
        baselines=("CrossSectionMomentum_20", "BuyAndHold"),
        trade_generating=True,
        v1_algo_notes="Linear factor + LightGBM Ranker, split/dividend/delisting/ADR-aware",
        trainer_symbols=(
            "packages.training.trainer.LinearTrainer",
            "packages.models.ranker.RankerTrainer",
        ),
    ),
    FamilyId.US_ETF_LONG_A_OR_SHORT_C: FamilySpec(
        id=FamilyId.US_ETF_LONG_A_OR_SHORT_C,
        market=Market.US,
        task="ETF long/short (leveraged/inverse ETF whitelist-excluded)",
        horizons_days=(5, 60),
        baselines=("BuyAndHold", "MovingAverage_5_20"),
        trade_generating=True,
        v1_algo_notes="Rule + LightGBM",
        trainer_symbols=(
            "packages.models.rule_gated.RuleGatedTrainer",
            "packages.models.gbm_scorer.GBMTrainer",
        ),
    ),
    FamilyId.MARKET_REGIME: FamilySpec(
        id=FamilyId.MARKET_REGIME,
        market=Market.CN,          # cross-market classification, but per-market instance
        task="regime classification: BULL_LOW / BULL_HIGH_VOL / SIDEWAYS_L / SIDEWAYS_H / BEAR / STRESS",
        horizons_days=(20,),
        baselines=("RuleCluster",),
        trade_generating=False,
        v1_algo_notes="Rule clustering; does NOT emit trades directly",
        trainer_symbols=("packages.models.regime.RegimeTrainer",),
    ),
}


def get_family(fid: FamilyId | str) -> FamilySpec:
    if isinstance(fid, str):
        fid = FamilyId(fid)
    return FAMILIES[fid]


def families_for_market(market: Market) -> list[FamilySpec]:
    return [f for f in FAMILIES.values() if f.market is market]


# ---------------------------------------------------------------------------
# Baselines — kept deliberately simple; only enough to beat / not beat.


@dataclass(frozen=True, slots=True)
class BuyAndHold:
    """Always-long baseline. Returns a constant positive score."""

    model_id: str = "baseline.buy_and_hold"
    version: str = "v1"
    horizon_days: int = 20
    feature_set_hash: str = "n/a"

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        return Prediction(
            score=1.0, horizon_days=self.horizon_days,
            model_id=self.model_id, model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


@dataclass(frozen=True, slots=True)
class FixedDCA:
    """Fixed dollar-cost averaging: constant target weight, ignores signal."""

    model_id: str = "baseline.fixed_dca"
    version: str = "v1"
    horizon_days: int = 60
    feature_set_hash: str = "n/a"
    target_weight: float = 1.0

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        return Prediction(
            score=self.target_weight, horizon_days=self.horizon_days,
            model_id=self.model_id, model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


@dataclass(frozen=True, slots=True)
class MovingAverageCross:
    """MA(short) vs MA(long) baseline. Score = +1 if short>long else 0."""

    short_window: int = 5
    long_window: int = 20
    model_id: str = "baseline.ma_5_20"
    version: str = "v1"
    horizon_days: int = 5
    feature_set_hash: str = "n/a"

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        # Feature-name contract: expects "ma_5" and "ma_20"; missing => 0.
        short = features.get(f"ma_{self.short_window}") or 0.0
        long_ = features.get(f"ma_{self.long_window}") or 0.0
        score = 1.0 if short > long_ else 0.0
        return Prediction(
            score=score, horizon_days=self.horizon_days,
            model_id=self.model_id, model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


@dataclass(frozen=True, slots=True)
class CrossSectionMomentum:
    """Uses precomputed `mom_20` feature (20-day return) as score."""

    lookback: int = 20
    model_id: str = "baseline.xs_momentum_20"
    version: str = "v1"
    horizon_days: int = 20
    feature_set_hash: str = "n/a"

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        mom = features.get(f"mom_{self.lookback}") or 0.0
        return Prediction(
            score=float(mom), horizon_days=self.horizon_days,
            model_id=self.model_id, model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


@dataclass(frozen=True, slots=True)
class RuleCluster:
    """MARKET_REGIME baseline: emits regime code via score index 0..5."""

    model_id: str = "baseline.regime_rule"
    version: str = "v1"
    horizon_days: int = 20
    feature_set_hash: str = "n/a"

    def predict_one(self, features: dict[str, float | None]) -> Prediction:
        ret_60 = features.get("ret_60") or 0.0
        vol_60 = features.get("vol_60") or 0.0
        if vol_60 > 0.05:
            code = 5.0 if ret_60 < -0.10 else 1.0  # STRESS vs BULL_HIGH_VOL
        elif ret_60 > 0.05:
            code = 0.0                              # BULL_LOW
        elif ret_60 < -0.05:
            code = 4.0                              # BEAR
        else:
            code = 2.0 if vol_60 < 0.02 else 3.0    # SIDEWAYS_L / _H
        return Prediction(
            score=code, horizon_days=self.horizon_days,
            model_id=self.model_id, model_version=self.version,
            feature_set_hash=self.feature_set_hash,
        )


BASELINE_REGISTRY: dict[str, Callable[[], Model]] = {
    "BuyAndHold":              lambda: BuyAndHold(),
    "FixedDCA":                lambda: FixedDCA(),
    "MovingAverage_5_20":      lambda: MovingAverageCross(),
    "CrossSectionMomentum_20": lambda: CrossSectionMomentum(),
    "RuleCluster":             lambda: RuleCluster(),
}


def make_baseline(name: str) -> Model:
    factory = BASELINE_REGISTRY.get(name)
    if factory is None:
        raise KeyError(f"unknown baseline: {name}")
    return factory()
