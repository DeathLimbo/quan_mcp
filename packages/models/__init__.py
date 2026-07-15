"""Model registry + Champion-Challenger state machine.

States (spec §模型注册):  DRAFT → CANDIDATE → SHADOW → PRODUCTION → RETIRED
"""
from packages.models.registry import (
    ModelState, ModelRecord, ModelRegistry, ModelTransitionError,
    InMemoryModelRegistry,
)
from packages.models.base import Model, Prediction
from packages.models.gbm_scorer import GBMTrainer, Stump, TrainedGBMModel
from packages.models.isotonic import (
    CalibratedModel, IsotonicCalibrator, IsotonicTrainer,
)
from packages.models.ranker import RankedGroup, RankerTrainer, TrainedRanker
from packages.models.rule_gated import (
    Rule, RuleGatedModel, RuleGatedTrainer, RuleVerdict,
    drawdown_short_rule, ma_trend_long_rule,
)
from packages.models.regime import (
    REGIME_NAMES,
    REGIME_BULL_LOW, REGIME_BULL_HIGH_VOL, REGIME_SIDEWAYS_L,
    REGIME_SIDEWAYS_H, REGIME_BEAR, REGIME_STRESS,
    RegimeThresholds, RegimeTrainer, TrainedRegimeClassifier,
)

__all__ = [
    "ModelState", "ModelRecord", "ModelRegistry", "ModelTransitionError",
    "InMemoryModelRegistry",
    "Model", "Prediction",
    "GBMTrainer", "Stump", "TrainedGBMModel",
    "CalibratedModel", "IsotonicCalibrator", "IsotonicTrainer",
    "RankedGroup", "RankerTrainer", "TrainedRanker",
    "Rule", "RuleGatedModel", "RuleGatedTrainer", "RuleVerdict",
    "drawdown_short_rule", "ma_trend_long_rule",
    "REGIME_NAMES", "REGIME_BULL_LOW", "REGIME_BULL_HIGH_VOL",
    "REGIME_SIDEWAYS_L", "REGIME_SIDEWAYS_H", "REGIME_BEAR", "REGIME_STRESS",
    "RegimeThresholds", "RegimeTrainer", "TrainedRegimeClassifier",
]
