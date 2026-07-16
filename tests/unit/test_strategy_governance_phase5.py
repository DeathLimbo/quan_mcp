"""Strategy governance Phase 5 tests (issue #10): shadow forward-tracking +
drift auto-suspend.

Covers the forward-tracking layer (predictions recorded white-on-black,
settled against realised returns, honest live IC) and the fail-closed drift
auto-suspender (collapsed live IC -> suspend without a human).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from packages.audit.record import InMemoryAuditSink
from packages.common.instrument_id import Market
from packages.persistence.strategy_governance import (
    _metadata,
    SqlChangeRequestRepository,
    SqlEvaluationRunRepository,
    SqlPromotionDecisionRepository,
    SqlStrategyVersionRepository,
)
from packages.strategy_governance import (
    DriftAutoSuspendConfig,
    DriftAutoSuspender,
    ShadowTracker,
    StrategyState,
)
from packages.strategy_governance.service import StrategyGovernanceService


def _engine():
    eng = sa.create_engine("sqlite:///:memory:")
    _metadata.create_all(eng)
    return eng


def _svc(eng=None) -> StrategyGovernanceService:
    eng = eng or _engine()
    return StrategyGovernanceService(
        versions=SqlStrategyVersionRepository(eng),
        change_requests=SqlChangeRequestRepository(eng),
        eval_runs=SqlEvaluationRunRepository(eng),
        decisions=SqlPromotionDecisionRepository(eng),
        audit=InMemoryAuditSink(), schemas={},
    )


# --------------------------------------------------------------------------- #
# 1. ShadowTracker record + settle + live IC
# --------------------------------------------------------------------------- #
def test_shadow_tracker_record_and_settle():
    t = ShadowTracker()
    for i in range(12):
        t.record_prediction(
            strategy_id="s1", version="v1", instrument_id=f"inst{i}",
            as_of=date(2026, 5, 1) + timedelta(days=i), horizon_days=20,
            expected_return=0.05 if i % 2 == 0 else -0.03,
        )

    def actual_fn(iid, as_of, settle):
        idx = int(iid.replace("inst", ""))
        return 0.04 if idx % 2 == 0 else -0.02  # correlated -> IC positive

    settled = t.settle_due(as_of=date(2026, 7, 16), actual_return_fn=actual_fn)
    assert len(settled) == 12
    tr = t.live_track_record(strategy_id="s1")
    assert tr["n_settled"] == 12
    assert tr["ic"] > 0  # correlated preds/actuals
    assert 0.0 <= tr["hit_rate"] <= 1.0


def test_shadow_tracker_unsettled_excluded():
    t = ShadowTracker()
    # prediction made today, horizon 20 -> not yet due
    t.record_prediction(
        strategy_id="s1", version="v1", instrument_id="inst0",
        as_of=date(2026, 7, 16), horizon_days=20, expected_return=0.05,
    )
    settled = t.settle_due(
        as_of=date(2026, 7, 16),
        actual_return_fn=lambda *a: 0.01,
    )
    assert settled == []
    tr = t.live_track_record(strategy_id="s1")
    assert tr["n_settled"] == 0


def test_shadow_tracker_empty_record():
    t = ShadowTracker()
    tr = t.live_track_record(strategy_id="s1")
    assert tr == {"ic": 0.0, "hit_rate": 0.0, "rmse": 0.0,
                  "max_drawdown": 0.0, "n_settled": 0.0}


# --------------------------------------------------------------------------- #
# 2. DriftAutoSuspender
# --------------------------------------------------------------------------- #
def test_drift_suspender_low_ic_suspends():
    sus = DriftAutoSuspender(DriftAutoSuspendConfig(min_live_ic=0.02, min_sample=5))
    track = {"ic": -0.15, "hit_rate": 0.3, "rmse": 0.2,
             "max_drawdown": -0.05, "n_settled": 20.0}
    suspend, reason = sus.should_suspend(track)
    assert suspend is True
    assert "edge collapsed" in reason


def test_drift_suspender_drawdown_breach_suspends():
    sus = DriftAutoSuspender(DriftAutoSuspendConfig(min_live_ic=-1.0,
                                                    max_drawdown=-0.10,
                                                    min_sample=5))
    track = {"ic": 0.5, "hit_rate": 0.6, "rmse": 0.1,
             "max_drawdown": -0.20, "n_settled": 20.0}
    suspend, reason = sus.should_suspend(track)
    assert suspend is True
    assert "drawdown" in reason


def test_drift_suspender_good_ic_no_suspend():
    sus = DriftAutoSuspender(DriftAutoSuspendConfig(min_live_ic=0.02, min_sample=5))
    track = {"ic": 0.15, "hit_rate": 0.6, "rmse": 0.1,
             "max_drawdown": -0.05, "n_settled": 20.0}
    suspend, _ = sus.should_suspend(track)
    assert suspend is False


def test_drift_suspender_insufficient_sample_no_suspend():
    sus = DriftAutoSuspender(DriftAutoSuspendConfig(min_live_ic=0.02, min_sample=50))
    track = {"ic": -0.5, "hit_rate": 0.2, "rmse": 0.3,
             "max_drawdown": -0.2, "n_settled": 5.0}
    suspend, reason = sus.should_suspend(track)
    assert suspend is False
    assert "insufficient sample" in reason


# --------------------------------------------------------------------------- #
# 3. service start_shadow + drift auto-suspend
# --------------------------------------------------------------------------- #
def _seed_strategy_at_shadow(svc, sid="s1"):
    """Walk a strategy DRAFT -> VALIDATED -> BACKTESTED -> SHADOW."""
    import hashlib, json
    from packages.strategy_governance import (
        ParameterSetVersion, StrategyVersion,
    )
    vals = {"x": 1}
    ps = ParameterSetVersion(values=vals, schema_version="v1",
                             content_hash=hashlib.sha256(
                                 json.dumps(vals, sort_keys=True).encode()
                             ).hexdigest(), created_by="t")
    svc._v.save(StrategyVersion(
        strategy_id=sid, version="v1", parent_version=None, market=Market.CN,
        horizon_days=20, state=StrategyState.BACKTESTED, parameter_set=ps,
        feature_set_hash="fs",
    ))
    # seed a passing eval (baseline_gate=1.0) so SHADOW transition is allowed
    svc.record_evaluation(
        strategy_id=sid, version="v1", window_start="2024-01-01",
        window_end="2025-01-01", regime_slices=("bull",),
        metrics={"ic": 0.12, "baseline_gate": 1.0},
    )
    return svc.start_shadow(strategy_id=sid, version="v1", decided_by="ops")


def test_service_start_shadow():
    svc = _svc()
    v = _seed_strategy_at_shadow(svc)
    assert v.state is StrategyState.SHADOW


def test_service_check_drift_and_suspend_triggers():
    """A strategy in SHADOW with a collapsed live IC gets auto-suspended."""
    svc = _svc()
    _seed_strategy_at_shadow(svc)
    # record 12 predictions (all bullish) that ALL miss (actuals bearish)
    for i in range(12):
        svc.record_shadow_prediction(
            strategy_id="s1", version="v1", instrument_id=f"inst{i}",
            as_of=date(2026, 5, 1) + timedelta(days=i), horizon_days=20,
            expected_return=0.05,   # predicted up
        )
    svc.settle_shadow_outcomes(
        as_of=date(2026, 7, 16),
        actual_return_fn=lambda iid, a, b: -0.04,  # actual down -> IC negative
    )
    tr = svc.get_live_track_record(strategy_id="s1")
    assert tr["n_settled"] == 12
    assert tr["ic"] <= 0  # all wrong direction

    suspended, reason = svc.check_drift_and_suspend(
        strategy_id="s1", version="v1",
    )
    assert suspended is True
    assert "edge collapsed" in reason or "drawdown" in reason
    # strategy is now SUSPENDED
    v = svc.get_version("s1", "v1")
    assert v.state is StrategyState.SUSPENDED


def test_service_check_drift_no_suspend_when_good():
    svc = _svc()
    _seed_strategy_at_shadow(svc)
    for i in range(12):
        svc.record_shadow_prediction(
            strategy_id="s1", version="v1", instrument_id=f"inst{i}",
            as_of=date(2026, 5, 1) + timedelta(days=i), horizon_days=20,
            expected_return=0.05 if i % 2 == 0 else -0.03,
        )
    svc.settle_shadow_outcomes(
        as_of=date(2026, 7, 16),
        actual_return_fn=lambda iid, a, b: (
            0.04 if int(iid.replace("inst", "")) % 2 == 0 else -0.02
        ),
    )
    suspended, _ = svc.check_drift_and_suspend(strategy_id="s1", version="v1")
    assert suspended is False
    v = svc.get_version("s1", "v1")
    assert v.state is StrategyState.SHADOW  # unchanged
