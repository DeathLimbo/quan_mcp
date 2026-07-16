"""Strategy governance Phase 4 tests (issue #10): factor governance.

Covers FactorVersion immutability, PIT availability (leakage guard),
dependency check, filter_available_factors, service register/retire/leakage,
evaluator fold-level filtering, and incremental contribution.
"""
from __future__ import annotations

import dataclasses
import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

import packages.features.basics  # noqa: F401
from packages.audit.record import InMemoryAuditSink
from packages.common.instrument_id import Market, parse_instrument_id
from packages.data_sources.contracts import Bar
from packages.persistence.strategy_governance import (
    _metadata,
    SqlChangeRequestRepository,
    SqlEvaluationRunRepository,
    SqlFactorVersionRepository,
    SqlPromotionDecisionRepository,
    SqlStrategyVersionRepository,
)
from packages.strategy_governance import (
    FactorState,
    FactorVersion,
    StrategyEvaluator,
    filter_available_factors,
    validate_factor_availability,
    validate_factor_dependencies,
    FactorDependencyError,
    FactorLeakageError,
)
from packages.strategy_governance.service import StrategyGovernanceService

PV_FEATURES = ("ret_1d", "ret_5d", "ret_20d", "vol_20d", "rsi_14d",
               "atr_14d", "max_drawdown_20d", "price_ma_dev_20d")
IID = parse_instrument_id("CN.CN_FUND.FUND.TEST")


def _bars(n: int = 320, seed: int = 42) -> list[Bar]:
    rng = random.Random(seed)
    bars: list[Bar] = []
    px = 100.0
    start = date(2024, 1, 1)
    for i in range(n):
        ret = 0.0008 + 0.012 * math.sin(i / 13.0) + rng.gauss(0, 0.011)
        px *= (1.0 + ret)
        d = start + timedelta(days=i)
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


def _factor(fid: str, available_from: date,
            deps: tuple[str, ...] = ()) -> FactorVersion:
    return FactorVersion(
        factor_id=fid, version="v1", definition_hash=f"h_{fid}",
        available_from=available_from, dependencies=deps,
    )


# --------------------------------------------------------------------------- #
# 1. immutability + availability
# --------------------------------------------------------------------------- #
def test_factor_version_is_frozen():
    f = _factor("ret_1d", date(2024, 1, 1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.available_from = date(2025, 1, 1)  # type: ignore[misc]


def test_availability_leak_rejected():
    f = _factor("late", date(2024, 6, 1))
    with pytest.raises(FactorLeakageError, match="future-function leak"):
        validate_factor_availability(f, date(2024, 3, 1))


def test_availability_ok():
    f = _factor("early", date(2024, 1, 1))
    validate_factor_availability(f, date(2024, 3, 1))  # no raise


# --------------------------------------------------------------------------- #
# 2. dependencies
# --------------------------------------------------------------------------- #
def test_dependencies_missing_rejected():
    f = _factor("composite", date(2024, 1, 1), deps=("ret_1d", "vol_20d"))
    with pytest.raises(FactorDependencyError, match="unavailable"):
        validate_factor_dependencies(f, {"ret_1d"})  # vol_20d missing


def test_dependencies_satisfied():
    f = _factor("composite", date(2024, 1, 1), deps=("ret_1d", "vol_20d"))
    validate_factor_dependencies(f, {"ret_1d", "vol_20d"})  # no raise


# --------------------------------------------------------------------------- #
# 3. filter_available_factors
# --------------------------------------------------------------------------- #
def test_filter_returns_only_available_active():
    factors = [
        _factor("early", date(2024, 1, 1)),
        _factor("late", date(2024, 9, 1)),
    ]
    avail_early = filter_available_factors(factors, date(2024, 3, 1))
    assert [f.factor_id for f in avail_early] == ["early"]
    avail_late = filter_available_factors(factors, date(2024, 12, 1))
    assert {f.factor_id for f in avail_late} == {"early", "late"}


def test_filter_excludes_retired():
    factors = [
        _factor("active", date(2024, 1, 1)),
        FactorVersion(factor_id="retired", version="v1", definition_hash="h",
                      available_from=date(2024, 1, 1),
                      state=FactorState.RETIRED),
    ]
    avail = filter_available_factors(factors, date(2024, 6, 1))
    assert [f.factor_id for f in avail] == ["active"]


# --------------------------------------------------------------------------- #
# 4. service register / retire / leakage
# --------------------------------------------------------------------------- #
def _svc(eng=None) -> StrategyGovernanceService:
    eng = eng or _engine()
    return StrategyGovernanceService(
        versions=SqlStrategyVersionRepository(eng),
        change_requests=SqlChangeRequestRepository(eng),
        eval_runs=SqlEvaluationRunRepository(eng),
        decisions=SqlPromotionDecisionRepository(eng),
        audit=InMemoryAuditSink(), schemas={},
        evaluators={"t": _evaluator()},
        factors=SqlFactorVersionRepository(eng),
    )


def test_service_register_and_retire_factor():
    svc = _svc()
    f = svc.register_factor(
        factor_id="ret_1d", version="v1", definition_hash="h",
        available_from=date(2024, 1, 1), created_by="ops",
    )
    assert f.state is FactorState.ACTIVE
    assert len(svc.list_factors()) == 1
    ok = svc.retire_factor(factor_id="ret_1d", version="v1",
                           decided_by="ops", reason="dep")
    assert ok is True
    assert len(svc.list_factors()) == 0  # retired excluded from active list


def test_service_check_factor_leakage():
    svc = _svc()
    svc.register_factor(factor_id="early", version="v1", definition_hash="h1",
                        available_from=date(2024, 1, 1), created_by="ops")
    svc.register_factor(factor_id="late", version="v1", definition_hash="h2",
                        available_from=date(2024, 9, 1), created_by="ops")
    leaks = svc.check_factor_leakage(as_of=date(2024, 3, 1),
                                     factor_ids=["early", "late"])
    assert leaks == ["late"]
    leaks2 = svc.check_factor_leakage(as_of=date(2024, 12, 1),
                                      factor_ids=["early", "late"])
    assert leaks2 == []


# --------------------------------------------------------------------------- #
# 5. evaluator fold-level PIT filtering
# --------------------------------------------------------------------------- #
def test_evaluator_fold_level_filtering_runs():
    """With factor_versions, early folds train on fewer features (only those
    available at the fold's training end). The run must still produce folds."""
    bars = _bars()
    # half the features available early, half late (mid-sample)
    mid = bars[len(bars) // 2].market_local_date
    fv = [_factor(fn, date(2024, 1, 1)) for fn in PV_FEATURES[:4]] + \
         [_factor(fn, mid) for fn in PV_FEATURES[4:]]
    res = _evaluator().evaluate(
        bars, strategy_id="t", version="v1", factor_versions=fv,
    )
    assert res.n_folds > 0
    assert res.n_total_predictions > 0
    assert -1.0 <= res.ic <= 1.0


def test_evaluator_without_factor_versions_uses_all_features():
    """No factor_versions -> all features used everywhere (back-compat)."""
    res = _evaluator().evaluate(_bars(), strategy_id="t", version="v1")
    assert res.n_folds > 0


# --------------------------------------------------------------------------- #
# 6. incremental contribution
# --------------------------------------------------------------------------- #
def test_service_check_incremental_contribution():
    svc = _svc()
    # seed a strategy version to evaluate against
    import hashlib, json
    from packages.strategy_governance import ParameterSetVersion, StrategyVersion, StrategyState
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
    fv = [_factor(fn, date(2024, 1, 1)) for fn in PV_FEATURES]
    result = svc.check_incremental_contribution(
        strategy_id="t", version="v1", bars=_bars(),
        factor_id="ret_1d", factor_versions=fv,
    )
    assert "incremental_ic" in result
    assert "ic_full" in result and "ic_without" in result
    assert isinstance(result["incremental_ic"], float)
    # incremental = full - without
    assert abs(result["incremental_ic"]
               - (result["ic_full"] - result["ic_without"])) < 1e-9
