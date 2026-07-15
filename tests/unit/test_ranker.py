"""Unit tests for the pairwise Ranker family (§108 CN/US_EQUITY_CROSS_SECTION_B).

We simulate a small cross-section of names on each date. Each name has two
features (value + quality). The forward return is a monotone combination
of the two features plus noise; ordering names by that combination should
match ordering by forward return.

The test asserts that:
1. The trained ranker's per-day rank correlation with truth is materially
   positive on the holdout.
2. Selecting the top-K names each day yields a positive long-only PnL
   larger than random (baseline: alphabetical order = essentially random).
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from packages.datasets.builder import DatasetRow
from packages.models.ranker import RankedGroup, RankerTrainer


NAMES = ("AAA", "BBB", "CCC", "DDD", "EEE")
FEATURES = ("value", "quality")


def _make_universe(*, n_days: int, seed: int) -> list[RankedGroup]:
    rng = random.Random(seed)
    groups: list[RankedGroup] = []
    d0 = date(2025, 1, 6)
    for day in range(n_days):
        as_of = d0 + timedelta(days=day)
        rows: list[DatasetRow] = []
        for name in NAMES:
            v = rng.uniform(-1.0, 1.0)
            q = rng.uniform(-1.0, 1.0)
            # Truth: forward return = 0.6*v + 0.4*q + noise
            y = 0.6 * v + 0.4 * q + rng.gauss(0.0, 0.05)
            rows.append(DatasetRow(
                as_of_date=as_of,
                features={"value": v, "quality": q},
                label=y,
                feature_set_hash="hRANK",
            ))
        groups.append(RankedGroup(group_key=as_of.isoformat(),
                                   rows=tuple(rows)))
    return groups


def _daily_rank_correlation(group: RankedGroup, predictor) -> float:
    """Spearman rho between predicted and true ordering inside one group."""
    scored = []
    for r in group.rows:
        try:
            s = predictor(r.features)
        except Exception:
            return 0.0
        scored.append((s, r.label))
    scored.sort(key=lambda p: p[0])
    ranks_pred = {id(p): i for i, p in enumerate(scored)}
    scored_true = sorted(scored, key=lambda p: p[1])
    ranks_true = {id(p): i for i, p in enumerate(scored_true)}
    n = len(scored)
    if n < 2:
        return 0.0
    ss = sum(
        (ranks_pred[id(p)] - ranks_true[id(p)]) ** 2 for p in scored
    )
    return 1.0 - (6.0 * ss) / (n * (n * n - 1))


def test_ranker_trainer_learns_positive_rank_correlation():
    train_groups = _make_universe(n_days=120, seed=11)
    holdout = _make_universe(n_days=40, seed=99)

    trainer = RankerTrainer(list(FEATURES), horizon_days=5,
                            num_rounds=30, learning_rate=0.15)
    ranker = trainer.fit(train_groups, model_id="rank.xs.b", version="v1")

    ic_values: list[float] = []
    for g in holdout:
        rho = _daily_rank_correlation(
            g, lambda f: ranker.predict_one(f).score,
        )
        ic_values.append(rho)
    mean_ic = sum(ic_values) / len(ic_values)
    assert mean_ic > 0.2, f"ranker mean per-day IC too low: {mean_ic}"


def test_ranker_topk_beats_random_selection():
    train_groups = _make_universe(n_days=120, seed=13)
    holdout = _make_universe(n_days=60, seed=77)

    trainer = RankerTrainer(list(FEATURES), horizon_days=5,
                            num_rounds=30, learning_rate=0.15)
    ranker = trainer.fit(train_groups, model_id="rank.xs.b.topk", version="v1")

    top_pnl = 0.0
    alpha_pnl = 0.0    # alphabetical baseline: always long the first name
    for g in holdout:
        # Rank names by predicted score, take top-2 each day.
        scored = []
        for r in g.rows:
            s = ranker.predict_one(r.features).score
            scored.append((s, r.label))
        scored.sort(key=lambda p: p[0], reverse=True)
        top_pnl += sum(y for _, y in scored[:2])
        # Baseline: first two names alphabetically (fixed set).
        alpha_pnl += sum(
            r.label for r in g.rows[:2] if r.label is not None
        )
    assert top_pnl > alpha_pnl, (
        f"top-K ranker PnL {top_pnl:.4f} did not beat alphabetical "
        f"baseline {alpha_pnl:.4f}"
    )


def test_ranker_predict_preserves_governance_metadata():
    groups = _make_universe(n_days=30, seed=1)
    trainer = RankerTrainer(list(FEATURES), horizon_days=20,
                            num_rounds=10, learning_rate=0.1)
    ranker = trainer.fit(groups, model_id="rank.xs.gov", version="v42")
    r0 = groups[0].rows[0]
    p = ranker.predict_one(r0.features)
    assert p.model_id == "rank.xs.gov"
    assert p.model_version == "v42"
    assert p.horizon_days == 20
    assert p.feature_set_hash == "hRANK"
    assert 0.0 <= p.score <= 1.0
