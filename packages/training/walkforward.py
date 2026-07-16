"""Walk-forward training pipeline — spec §15.1 时间切分 + §20 迭代频率.

Rolling-window walk-forward: for each test window, train on the preceding
train window, predict the test window, and collect out-of-sample predictions.
This produces the OOS prediction series needed for evaluation (§16) and
drift detection (§22).

The trainer is pluggable — pass a :class:`LinearTrainer` or
:class:`LightGBMTrainer` factory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Sequence

from packages.common.instrument_id import InstrumentId
from packages.data_sources.contracts import Bar
from packages.datasets.builder import build_dataset
from packages.models.base import Model
from packages.training.trainer import LinearTrainer


@dataclass(frozen=True, slots=True)
class OOSPrediction:
    as_of: date
    instrument_id: str
    score: float
    label: float | None
    model_version: str


@dataclass
class WalkForwardResult:
    predictions: list[OOSPrediction] = field(default_factory=list)
    model_versions: list[tuple[date, str, str]] = field(default_factory=list)  # (window_end, model_id, version)


def _window_bounds(
    anchor: date, train_days: int, test_days: int,
) -> tuple[date, date, date, date]:
    """Return (train_start, train_end, test_start, test_end) for a window anchored at ``anchor`` (test_end)."""
    test_end = anchor
    test_start = test_end - timedelta(days=test_days - 1)
    train_end = test_start - timedelta(days=1)
    train_start = train_end - timedelta(days=train_days - 1)
    return train_start, train_end, test_start, test_end


def walk_forward(
    bars_by_iid: dict[InstrumentId, Sequence[Bar]],
    feature_names: list[str],
    horizon_days: int,
    *,
    trainer_factory: Callable[[], object],
    start: date,
    end: date,
    train_days: int = 252,
    test_days: int = 21,
    step_days: int = 21,
) -> WalkForwardResult:
    """Run walk-forward training across [start, end].

    For each test window ending at ``anchor`` (stepped by ``step_days``):
    1. Build (features, label) rows for every instrument in the train window.
    2. Fit a fresh trainer instance.
    3. Build rows for the test window and predict each row.
    4. Collect OOS predictions.

    ``trainer_factory`` must return an object with
    ``fit(rows, *, model_id, version) -> Model`` (LinearTrainer / LightGBMTrainer).
    """
    result = WalkForwardResult()
    anchor = start + timedelta(days=train_days + test_days - 1)

    window_idx = 0
    while anchor <= end:
        window_idx += 1
        tr_start, tr_end, te_start, te_end = _window_bounds(anchor, train_days, test_days)

        # 1) build train rows across all instruments
        train_rows = []
        for iid, bars in bars_by_iid.items():
            rows = build_dataset(
                list(bars), feature_names,
                horizon_days=horizon_days,
                start=tr_start, end=tr_end,
            )
            train_rows.extend(rows)

        if not train_rows:
            anchor += timedelta(days=step_days)
            continue

        # 2) fit trainer
        trainer = trainer_factory()
        model_id = f"wf_{horizon_days}d_w{window_idx}"
        model = trainer.fit(train_rows, model_id=model_id)  # type: ignore[attr-defined]

        result.model_versions.append((te_end, model_id, model.version))  # type: ignore[attr-defined]

        # 3) build test rows + predict
        for iid, bars in bars_by_iid.items():
            test_rows = build_dataset(
                list(bars), feature_names,
                horizon_days=horizon_days,
                start=te_start, end=te_end,
            )
            for r in test_rows:
                if r.label is None:
                    continue
                try:
                    pred = model.predict_one(r.features)  # type: ignore[attr-defined]
                    result.predictions.append(OOSPrediction(
                        as_of=r.as_of_date,
                        instrument_id=iid.canonical(),
                        score=pred.score,
                        label=r.label,
                        model_version=model.version,  # type: ignore[attr-defined]
                    ))
                except Exception:
                    # missing feature at inference → skip (fail-closed)
                    continue

        anchor += timedelta(days=step_days)

    return result
