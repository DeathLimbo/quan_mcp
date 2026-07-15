"""Walk-forward test for the GBM (gradient boosted stumps) candidate family.

Closes §108 for the *LightGBM equity XS* family with a real learned model —
we deliberately choose a piecewise / threshold signal that a linear learner
cannot capture in a single feature but a shallow GBM ensemble can, then
verify the trained GBM beats the linear baselines through the §81.1 gate.

Signal (deterministic, seeded):
    next_ret = { +0.02  if ret_5d < -0.02,
                 -0.02  if ret_5d > +0.02,
                  0     otherwise } + N(0, 0.005)

This is a saturated mean-reversion response — a step function of the past
5-day return. A single linear coefficient on ``ret_5d`` cannot express both
the "flat middle" and "clipped tails" simultaneously, so Ridge underfits.
A gradient-boosted stump ensemble discovers the two thresholds and beats
the baselines materially.
"""
from __future__ import annotations

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
from packages.models import GBMTrainer
from packages.models.families import (
    BuyAndHold, CrossSectionMomentum, MovingAverageCross,
)
from packages.models.registry import (
    InMemoryModelRegistry, ModelRecord, ModelState,
)


IID = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "GBMSYN")
FEATURE_NAMES = ("ret_1d", "ret_5d", "ret_20d")
HORIZON = 5


def _synthesize_step_signal_bars(*, n: int = 320, seed: int = 7) -> list[Bar]:
    rng = random.Random(seed)
    closes: list[float] = [100.0]
    for i in range(1, n):
        ret_5d = closes[i - 1] / closes[i - 6] - 1.0 if i >= 5 else 0.0
        if ret_5d < -0.02:
            drift = 0.02
        elif ret_5d > 0.02:
            drift = -0.02
        else:
            drift = 0.0
        next_ret = drift + rng.gauss(0.0, 0.005)
        closes.append(max(closes[i - 1] * (1.0 + next_ret), 1.0))

    start = date(2024, 6, 3)   # Monday
    bars: list[Bar] = []
    cur = start
    for c in closes:
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


def _split(rows: list[DatasetRow], boundary: date
           ) -> tuple[list[DatasetRow], list[DatasetRow]]:
    tr = [r for r in rows if r.as_of_date <= boundary and r.label is not None]
    va = [r for r in rows if r.as_of_date > boundary and r.label is not None]
    return tr, va


def _metrics(rows: list[DatasetRow], predictor) -> dict[str, float]:
    pairs: list[tuple[float, float]] = []
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
    n = len(pairs)
    third = max(1, n // 3)
    ordered = sorted(pairs, key=lambda p: p[0])
    bottom = ordered[:third]
    top = ordered[-third:]
    net = sum(y for _, y in top) - sum(y for _, y in bottom)
    return {"ic": ic, "net_return": net}


def test_walk_forward_gbm_candidate_beats_baselines_and_promotes():
    bars = _synthesize_step_signal_bars()
    rows = build_dataset(
        bars, FEATURE_NAMES, horizon_days=HORIZON,
        start=bars[30].market_local_date,
        end=bars[-HORIZON - 1].market_local_date,
    )
    assert rows, "dataset builder produced no rows"

    boundary = rows[len(rows) * 2 // 3].as_of_date
    train_rows, holdout_rows = _split(rows, boundary)
    assert len(train_rows) >= 60
    assert len(holdout_rows) >= 30

    trainer = GBMTrainer(
        list(FEATURE_NAMES), horizon_days=HORIZON,
        num_rounds=40, learning_rate=0.1,
    )
    candidate = trainer.fit(train_rows, model_id="gbm.reversion_stumps", version="v1")
    assert candidate.stumps, "GBM produced no stumps"

    cand_metrics = _metrics(holdout_rows, lambda f: candidate.predict_one(f).score)
    baseline_metrics = {
        "BuyAndHold": _metrics(
            holdout_rows, lambda f: BuyAndHold().predict_one(f).score,
        ),
        "MovingAverage_5_20": _metrics(
            holdout_rows, lambda f: MovingAverageCross().predict_one(f).score,
        ),
        "CrossSectionMomentum_20": _metrics(
            holdout_rows,
            lambda f: CrossSectionMomentum(lookback=20).predict_one(
                {"mom_20": f.get("ret_20d")}
            ).score,
        ),
    }

    assert cand_metrics["ic"] > 0.05, f"GBM candidate IC too low: {cand_metrics}"
    assert cand_metrics["net_return"] > 0.0, (
        f"GBM candidate net_return non-positive: {cand_metrics}"
    )

    gate = beats_all_baselines(
        candidate_id=f"{candidate.model_id}@{candidate.version}",
        candidate_metrics=cand_metrics,
        baselines=baseline_metrics,
    )
    assert gate.passed, f"gate rejected GBM candidate: {gate.losses}"

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
    prod = reg.transition(
        candidate.model_id, candidate.version, ModelState.PRODUCTION,
        actor="approver", approval_id="APR-gbm-e2e",
    )
    assert prod.state is ModelState.PRODUCTION
