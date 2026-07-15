"""Drift detection (spec §89).

Three families, in this order of severity:
- Data drift: PSI + Kolmogorov–Smirnov on feature distributions vs training snapshot.
- Model drift: OOD share, prediction-distribution shift.
- Effect drift: rolling IC / AUC / drawdown vs walk-forward baseline.

All computations are pure functions on lists / arrays; no I/O, no timezone
handling. Consumers (drift-monitor worker) wire in cadence and thresholds
from ``configs/drift.yaml``.
"""
from packages.drift.metrics import (
    DriftLevel,
    DriftReport,
    ic_series,
    ks_stat,
    ood_share,
    prediction_shift_kl,
    psi,
)

__all__ = [
    "DriftLevel", "DriftReport",
    "psi", "ks_stat", "ood_share", "prediction_shift_kl", "ic_series",
]
