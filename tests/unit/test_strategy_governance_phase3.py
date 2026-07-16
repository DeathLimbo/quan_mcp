"""Strategy governance Phase 3 tests (issue #10): deterministic evaluation.

Covers the walk-forward evaluator: out-of-sample IC, regime slicing, cost/
drawdown, repro hash determinism, baseline gate (§81.1), and service
integration (evaluate records a run; SHADOW blocked without baseline gate).
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

import packages.features.basics  # noqa: F401  register default features
from packages.audit.record import InMemoryAuditSink
from packages.common.instrument_id import Market, parse_instrument_id
from packages.data_sources.contracts import Bar
from packages.persistence.strategy_governance import (
    _metadata,
    SqlChangeRequestRepository,
    SqlEvaluationRunRepository,
    SqlPromotionDecisionRepository,
    SqlStrategyVersionRepository,
)
from packages.strategy_governance import (
    StrategyEvaluator,
    StrategyState,
)
from packages.strategy_governance.service import StrategyGovernanceService

PV_FEATURES = ("ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
               "atr_14d", "max_drawdown_20d", "price_ma_dev_20d")
IID = parse_instrument_id("CN.CN_FUND.FUND.TEST")


def _bars(n: int = 320, seed: int = 42) -> list[Bar]:
    """Deterministic synthetic bars with drift + cycle + noise so features
    have signal (not all-zero)."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    px = 100.0
    start = date(2024, 1, 1)
    for i in range(n):
        # drift + sin cycle + noise -> non-trivial return structure
        ret = 0.0008 + 0.012 * math.sin(i / 13.0) + rng.gauss(0, 0.011)
        px *= (1.0 + ret)
        d = start + timedelta(days=i)
        # skip weekends so calendar-like
        if d.weekday() >= 5:
            continue
        o = px * (1 + rng.gauss(0, 0.002))
        h = max(px, o) * (1 + abs(rng.gauss(0, 0.003)))
        lo = min(px, o) * (1 - abs(rng.gauss(0, 0.003)))
        ts = datetime(d.year, d.month, d.day, 15, 0, tzinfo=timezone.utc)
        bars.append(Bar(
            instrument_id=IID, event_time_utc=ts, market_local_date=d,
            open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(lo)),
            close=Decimal(str(px)), volume=Decimal("1000000"),
            turnover=Decimal(str(px * 1_000_000)), adj_factor=Decimal("1.0"),
            available_at_utc=ts, source="test", calendar_version="v1",
            rule_version="v1",
        ))
    return bars


def _engine():
    eng = sa.create_engine("sqlite:///:memory:")
    _metadata.create_all(eng)
    return eng


def _evaluator() -> StrategyEvaluator:
    return StrategyEvaluator(
        feature_names=PV_FEATURES, horizon_days=20,
        train_min=120, num_boost_round=20,
    )


# --------------------------------------------------------------------------- #
# 1. walk-forward produces out-of-sample IC
# --------------------------------------------------------------------------- #
def test_walk_forward_produces_oos_metrics():
    res = _evaluator().evaluate(_bars(), strategy_id="t", version="v1")
    assert res.n_folds > 0, "expected at least one walk-forward fold"
    assert res.n_total_predictions > 0
    assert isinstance(res.ic, float)
    assert -1.0 <= res.ic <= 1.0
    assert 0.0 <= res.hit_rate <= 1.0
    assert res.rmse >= 0.0


# --------------------------------------------------------------------------- #
# 2. regime slicing
# --------------------------------------------------------------------------- #
def test_regime_ic_has_slices():
    res = _evaluator().evaluate(_bars(), strategy_id="t", version="v1")
    assert len(res.regime_ic) > 0
    # at least one regime has a finite IC
    assert any(math.isfinite(v) for v in res.regime_ic.values())
    # each fold tagged with a known regime
    assert all(f.regime in ("bull", "bear", "range") for f in res.folds)


# --------------------------------------------------------------------------- #
# 3. cost / drawdown
# --------------------------------------------------------------------------- #
def test_drawdown_nonpositive_and_sharpe_finite():
    res = _evaluator().evaluate(_bars(), strategy_id="t", version="v1")
    assert res.max_drawdown <= 0.0, "max drawdown must be <= 0"
    assert math.isfinite(res.sharpe)
    assert math.isfinite(res.net_return)


# --------------------------------------------------------------------------- #
# 4. repro hash determinism
# --------------------------------------------------------------------------- #
def test_repro_hash_deterministic():
    ev = _evaluator()
    bars = _bars()
    r1 = ev.evaluate(bars, strategy_id="t", version="v1")
    r2 = ev.evaluate(bars, strategy_id="t", version="v1")
    assert r1.repro_hash != ""
    assert r1.repro_hash == r2.repro_hash
    # different inputs -> different hash
    r3 = ev.evaluate(_bars(seed=99), strategy_id="t", version="v1")
    assert r3.repro_hash != r1.repro_hash


# --------------------------------------------------------------------------- #
# 5. baseline gate (§81.1)
# --------------------------------------------------------------------------- #
def test_baseline_gate_rejects_weak_candidate():
    """A baseline that is strictly better than the candidate on every key
    forces the gate to fail."""
    res = _evaluator().evaluate(
        _bars(), strategy_id="t", version="v1",
        baseline_metrics={"strong_baseline": {"ic": 0.99, "net_return": 0.99}},
    )
    assert res.baseline_gate_passed is False
    assert len(res.baseline_losses) > 0


def test_baseline_gate_passes_strong_candidate():
    """A deliberately weak baseline lets the candidate pass."""
    res = _evaluator().evaluate(
        _bars(), strategy_id="t", version="v1",
        baseline_metrics={"weak": {"ic": -0.99, "net_return": -0.99}},
    )
    assert res.baseline_gate_passed is True
    assert res.baseline_losses == ()


# --------------------------------------------------------------------------- #
# 6. service.evaluate records an EvaluationRun with baseline_gate in metrics
# --------------------------------------------------------------------------- #
def test_service_evaluate_records_run_with_baseline_gate():
    eng = _engine()
    svc = StrategyGovernanceService(
        versions=SqlStrategyVersionRepository(eng),
        change_requests=SqlChangeRequestRepository(eng),
        eval_runs=SqlEvaluationRunRepository(eng),
        decisions=SqlPromotionDecisionRepository(eng),
        audit=InMemoryAuditSink(),
        schemas={},
        evaluators={"t": _evaluator()},
    )
    # seed a strategy version to evaluate
    from packages.strategy_governance import ParameterSetVersion
    import hashlib, json
    vals = {"x": 1}
    ps = ParameterSetVersion(values=vals, schema_version="v1",
                             content_hash=hashlib.sha256(
                                 json.dumps(vals, sort_keys=True).encode()
                             ).hexdigest(), created_by="t")
    from packages.strategy_governance import StrategyVersion
    svc._v.save(StrategyVersion(
        strategy_id="t", version="v1", parent_version=None, market=Market.CN,
        horizon_days=20, state=StrategyState.BACKTESTED, parameter_set=ps,
        feature_set_hash="fs",
    ))
    run, result = svc.evaluate(
        strategy_id="t", version="v1", bars=_bars(),
        baseline_metrics={"weak": {"ic": -0.99, "net_return": -0.99}},
    )
    assert run.status.value == "COMPLETED"
    assert "ic" in run.metrics
    assert run.metrics.get("baseline_gate") == 1.0
    assert run.repro_hash == result.repro_hash
    # persisted
    runs = svc.list_evaluations("t", "v1")
    assert len(runs) == 1


# --------------------------------------------------------------------------- #
# 7. SHADOW transition blocked without a passing baseline gate
# --------------------------------------------------------------------------- #
def test_service_shadow_blocked_without_passing_baseline_gate():
    """A COMPLETED eval whose baseline_gate is 0.0 (candidate lost) must
    block the SHADOW transition (§81.1, Phase 3)."""
    eng = _engine()
    svc = StrategyGovernanceService(
        versions=SqlStrategyVersionRepository(eng),
        change_requests=SqlChangeRequestRepository(eng),
        eval_runs=SqlEvaluationRunRepository(eng),
        decisions=SqlPromotionDecisionRepository(eng),
        audit=InMemoryAuditSink(),
        schemas={},
        evaluators={"t": _evaluator()},
    )
    from packages.strategy_governance import ParameterSetVersion, StrategyVersion
    import hashlib, json
    vals = {"x": 1}
    ps = ParameterSetVersion(values=vals, schema_version="v1",
                             content_hash=hashlib.sha256(
                                 json.dumps(vals, sort_keys=True).encode()
                             ).hexdigest(), created_by="t")
    svc._v.save(StrategyVersion(
        strategy_id="t", version="v1", parent_version=None, market=Market.CN,
        horizon_days=20, state=StrategyState.BACKTESTED, parameter_set=ps,
        feature_set_hash="fs",
    ))
    # record an eval that FAILED the baseline gate
    svc.record_evaluation(
        strategy_id="t", version="v1", window_start="2024-01-01",
        window_end="2025-01-01", regime_slices=("bull",),
        metrics={"ic": 0.01, "baseline_gate": 0.0},   # lost to baseline
    )
    from packages.strategy_governance.errors import StrategyGovernanceError
    with pytest.raises(StrategyGovernanceError, match="beat baselines"):
        svc.transition(strategy_id="t", version="v1",
                       to=StrategyState.SHADOW, decided_by="ops")
