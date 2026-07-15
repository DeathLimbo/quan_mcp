"""End-to-end walk-forward test: real Ridge candidate beats baselines then
passes §81.1 promotion gate.

This test wires together the primitives that already exist in the tree:
- Synthesizes a bar series with a *learnable* mean-reversion signal (returns
  regress on ``ret_5d`` with a known coefficient plus a small noise term so
  the signal is real but not deterministic).
- Builds a walk-forward dataset via ``packages.datasets.builder``.
- Trains a ``LinearTrainer`` (Ridge regression, closed-form) on the train
  slice.
- Computes candidate + baseline metrics (IC + net return) on the *same*
  holdout slice — no data leakage.
- Feeds both metric bundles through ``beats_all_baselines``.
- Uses the resulting ``PromotionResult`` to drive the model registry from
  CANDIDATE to SHADOW to PRODUCTION.

This is the first test in the tree that closes the §81.1 loop with a real
trained model rather than a stub — the candidate would fail the strict >
comparison if the trainer were broken, and it fails without the gate ever
being consulted if the registry short-circuits.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue,
)
from packages.data_sources.contracts import Bar
from packages.datasets.builder import DatasetRow, build_dataset
from packages.evaluation.metrics import information_coefficient
from packages.evaluation.promotion import beats_all_baselines
from packages.models.families import (
    BuyAndHold, CrossSectionMomentum, MovingAverageCross,
)
from packages.models.registry import (
    InMemoryModelRegistry, ModelRecord, ModelState,
)
from packages.training.trainer import LinearTrainer


IID = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "SYNTH")
FEATURE_NAMES = ("ret_1d", "ret_5d", "ret_20d")
HORIZON = 5


def _synthesize_bars(*, n: int = 240, seed: int = 42) -> list[Bar]:
    """Generate a bar series where ``next_return = -0.4 * ret_5d + noise``.

    Mean-reversion coefficient is negative and material (0.4). A trained
    Ridge on ret_5d will discover roughly this coefficient, and its IC on
    the holdout will beat BuyAndHold (~0) and simple momentum baselines
    (which have the wrong sign).
    """
    rng = random.Random(seed)
    closes: list[float] = [100.0]
    for i in range(1, n):
        if i >= 5:
            ret_5d = closes[i - 1] / closes[i - 6] - 1.0
        else:
            ret_5d = 0.0
        # Signal: strong mean reversion in the 5-day window + noise.
        next_ret = -0.4 * ret_5d + rng.gauss(0.0, 0.010)
        closes.append(max(closes[i - 1] * (1.0 + next_ret), 1.0))

    start = date(2025, 1, 6)   # a Monday
    bars: list[Bar] = []
    cur = start
    for c in closes:
        # Advance to next weekday; keep it simple: use calendar days but tag
        # available_at_utc after the close.
        while cur.weekday() >= 5:
            cur = cur + timedelta(days=1)
        et = datetime(cur.year, cur.month, cur.day, 21, tzinfo=timezone.utc)
        bars.append(Bar(
            instrument_id=IID, event_time_utc=et, market_local_date=cur,
            open=Decimal(f"{c:.4f}"), high=Decimal(f"{c:.4f}"),
            low=Decimal(f"{c:.4f}"), close=Decimal(f"{c:.4f}"),
            volume=Decimal("1000"), turnover=None,
            adj_factor=Decimal("1"), available_at_utc=et,
            source="synth", calendar_version="v0", rule_version="v0",
        ))
        cur = cur + timedelta(days=1)
    return bars


def _split_rows_by_date(rows: list[DatasetRow], boundary: date
                        ) -> tuple[list[DatasetRow], list[DatasetRow]]:
    train = [r for r in rows if r.as_of_date <= boundary and r.label is not None]
    valid = [r for r in rows if r.as_of_date > boundary and r.label is not None]
    return train, valid


def _score_and_metrics(rows: list[DatasetRow], predictor) -> dict[str, float]:
    """Compute Spearman IC + net return on the given holdout rows.

    ``predictor(features)`` must return a float score; higher = more bullish.
    net_return = sum of forward returns of the top-third scored rows minus
    bottom-third, i.e. a long-short 33/33 spread. This mirrors a naive
    daily-rebalance long-short PnL and is bounded even for large samples.
    """
    pairs: list[tuple[float, float]] = []   # (score, label)
    for r in rows:
        try:
            s = predictor(r.features)
        except Exception:
            continue
        if r.label is None or s is None:
            continue
        pairs.append((float(s), float(r.label)))
    if not pairs:
        return {"ic": 0.0, "net_return": 0.0}

    scores = [s for s, _ in pairs]
    labels = [y for _, y in pairs]
    ic = information_coefficient(scores, labels)

    # Long top-third, short bottom-third across the *time series*.
    n = len(pairs)
    third = max(1, n // 3)
    sorted_by_score = sorted(pairs, key=lambda p: p[0])
    bottom = sorted_by_score[:third]
    top = sorted_by_score[-third:]
    net_return = sum(y for _, y in top) - sum(y for _, y in bottom)
    return {"ic": ic, "net_return": net_return}


def test_walk_forward_ridge_candidate_beats_baselines_and_promotes():
    bars = _synthesize_bars()
    rows = build_dataset(
        bars, FEATURE_NAMES, horizon_days=HORIZON,
        start=bars[30].market_local_date,      # burn-in for 20d features
        end=bars[-HORIZON - 1].market_local_date,
    )
    assert rows, "dataset builder returned no rows"

    boundary = rows[len(rows) * 2 // 3].as_of_date
    train_rows, holdout_rows = _split_rows_by_date(rows, boundary)
    assert len(train_rows) >= 40, "not enough training rows"
    assert len(holdout_rows) >= 20, "not enough holdout rows"

    # 1) Train the Ridge candidate on train slice only.
    trainer = LinearTrainer(list(FEATURE_NAMES), horizon_days=HORIZON, lam=1e-3)
    candidate = trainer.fit(train_rows, model_id="ridge.reversion", version="v1")

    def _cand(features):
        p = candidate.predict_one(features)
        return p.score

    # 2) Compute metrics for candidate + three baselines on the SAME holdout.
    cand_metrics = _score_and_metrics(holdout_rows, _cand)
    baseline_metrics = {
        "BuyAndHold": _score_and_metrics(
            holdout_rows, lambda f: BuyAndHold().predict_one(f).score,
        ),
        "MovingAverage_5_20": _score_and_metrics(
            holdout_rows, lambda f: MovingAverageCross().predict_one(f).score,
        ),
        "CrossSectionMomentum_20": _score_and_metrics(
            holdout_rows,
            # Momentum uses ret_20d indirectly; give it what it looks for.
            lambda f: CrossSectionMomentum(lookback=20).predict_one(
                {"mom_20": f.get("ret_20d")}
            ).score,
        ),
    }

    # 3) The mean-reversion signal is real: candidate IC must be materially
    # positive and net_return must be positive on the holdout.
    assert cand_metrics["ic"] > 0.05, (
        f"candidate IC too low; got {cand_metrics}"
    )
    assert cand_metrics["net_return"] > 0.0, (
        f"candidate net_return non-positive; got {cand_metrics}"
    )

    # 4) Feed real metrics through the §81.1 gate.
    gate = beats_all_baselines(
        candidate_id=f"{candidate.model_id}@{candidate.version}",
        candidate_metrics=cand_metrics,
        baselines=baseline_metrics,
    )
    assert gate.passed, (
        f"gate rejected candidate against baselines: {gate.losses}"
    )

    # 5) Drive the registry from CANDIDATE -> SHADOW -> PRODUCTION.
    reg = InMemoryModelRegistry()
    rec = ModelRecord(
        model_id=candidate.model_id, version=candidate.version,
        market=Market.US, horizon_days=HORIZON,
        feature_set_hash=candidate.feature_set_hash, state=ModelState.DRAFT,
        created_at=datetime.now(timezone.utc), approved_by=None,
        approval_id=None,
    )
    reg.register(rec, artifact=candidate)
    reg.transition(candidate.model_id, candidate.version,
                    ModelState.CANDIDATE, actor="ops")
    reg.transition(candidate.model_id, candidate.version,
                    ModelState.SHADOW, actor="ops", promotion_gate=gate)
    rec_prod = reg.transition(
        candidate.model_id, candidate.version, ModelState.PRODUCTION,
        actor="approver", approval_id="APR-e2e",
    )
    assert rec_prod.state is ModelState.PRODUCTION
    assert reg.get_production(Market.US, HORIZON) is not None
