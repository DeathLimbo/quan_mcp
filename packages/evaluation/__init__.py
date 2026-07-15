"""Evaluation metrics (Sharpe, MaxDD, IC, hit rate, calibration) + §81.1 gate."""
from packages.evaluation.metrics import (
    sharpe_ratio, max_drawdown, hit_rate, information_coefficient,
    brier_score, isotonic_calibrate,
)
from packages.evaluation.promotion import (
    DEFAULT_GATE_KEYS, PromotionGateFailed, PromotionResult,
    beats_all_baselines, require_beats_all_baselines,
)

__all__ = [
    "sharpe_ratio", "max_drawdown", "hit_rate", "information_coefficient",
    "brier_score", "isotonic_calibrate",
    "DEFAULT_GATE_KEYS", "PromotionGateFailed", "PromotionResult",
    "beats_all_baselines", "require_beats_all_baselines",
]
