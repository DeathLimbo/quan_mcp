"""Tests for phase 2/3/4 modules — features, models, risk, portfolio, reporting, admin."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from packages.audit.record import AuditLog, InMemoryAuditSink
from packages.backtest.engine import BacktestConfig, run_daily_signal_backtest
from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.contracts import Bar
from packages.evaluation.metrics import (
    brier_score, hit_rate, information_coefficient, isotonic_calibrate,
    max_drawdown, sharpe_ratio,
)
from packages.evaluation.promotion import beats_all_baselines
from packages.features.featureset import FeatureSet
from packages.inference.service import Forecast, InferenceService, NoForecast, NoForecastReason
from packages.models.base import Prediction
from packages.models.registry import (
    InMemoryModelRegistry, ModelRecord, ModelState, ModelTransitionError,
)
from packages.portfolio.builder import PortfolioConfig, build_portfolio
from packages.reporting.render import (
    render_backtest_summary, render_daily_report, render_risk_trace,
)
from packages.risk.engine import RiskContext, RiskVerdict, default_engine


IID_AAPL = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")


def _passing_gate(candidate_id: str = "m1@v1"):
    """§81.1 gate helper: build a passing PromotionResult for tests."""
    return beats_all_baselines(
        candidate_id=candidate_id,
        candidate_metrics={"ic": 0.10, "net_return": 0.08},
        baselines={"BuyAndHold": {"ic": 0.02, "net_return": 0.03}},
    )
IID_MOUTAI = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")


def _bar(d: date, close: float, *, tzoffset_hours: int = 0) -> Bar:
    ts = datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone.utc) \
        + timedelta(hours=tzoffset_hours)
    return Bar(
        instrument_id=IID_AAPL,
        event_time_utc=ts,
        market_local_date=d,
        open=Decimal(str(close)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)),
        close=Decimal(str(close)),
        volume=Decimal("1000000"),
        turnover=None,
        adj_factor=None,
        available_at_utc=ts,
        source="test",
        calendar_version="cal-v0",
        rule_version="rule-v0",
    )


# ---- FeatureSet -------------------------------------------------------------

def test_featureset_content_hash_stable():
    fs1 = FeatureSet(names=("ret_1d",))
    fs2 = FeatureSet(names=("ret_1d",))
    assert fs1.content_hash == fs2.content_hash
    assert len(fs1.content_hash) == 64


def test_featureset_computes_ret_1d():
    fs = FeatureSet(names=("ret_1d",))
    bars = [_bar(date(2026, 1, i), 100.0 + i) for i in range(1, 6)]
    as_of = datetime(2026, 1, 10, tzinfo=timezone.utc)
    out = fs.compute(bars, as_of)
    # last close = 105, prev = 104 -> ret = 105/104 - 1
    assert out["ret_1d"] == pytest.approx(105 / 104 - 1)


# ---- Model registry --------------------------------------------------------

def test_registry_state_machine_requires_approval_for_production():
    reg = InMemoryModelRegistry()
    rec = ModelRecord(
        model_id="m1", version="v1", market=Market.US, horizon_days=5,
        feature_set_hash="abc", state=ModelState.DRAFT,
        created_at=datetime.now(timezone.utc), approved_by=None, approval_id=None,
    )
    reg.register(rec)
    reg.transition("m1", "v1", ModelState.CANDIDATE, actor="alice")
    with pytest.raises(ModelTransitionError, match="approval_id"):
        reg.transition("m1", "v1", ModelState.PRODUCTION, actor="alice",
                       promotion_gate=_passing_gate())
    reg.transition("m1", "v1", ModelState.PRODUCTION, actor="alice",
                   approval_id="APR-1", promotion_gate=_passing_gate())
    prod = reg.get_production(Market.US, 5)
    assert prod is not None and prod.model_id == "m1"


def test_registry_per_market_horizon_production_unique():
    reg = InMemoryModelRegistry()
    for i in (1, 2):
        rec = ModelRecord(
            model_id=f"m{i}", version="v1", market=Market.US, horizon_days=5,
            feature_set_hash="abc", state=ModelState.DRAFT,
            created_at=datetime.now(timezone.utc), approved_by=None, approval_id=None,
        )
        reg.register(rec)
        reg.transition(f"m{i}", "v1", ModelState.CANDIDATE, actor="ops")
    reg.transition("m1", "v1", ModelState.PRODUCTION, actor="ops",
                   approval_id="APR-1", promotion_gate=_passing_gate("m1@v1"))
    with pytest.raises(ModelTransitionError, match="already has PRODUCTION"):
        reg.transition("m2", "v1", ModelState.PRODUCTION, actor="ops",
                       approval_id="APR-2", promotion_gate=_passing_gate("m2@v1"))


# ---- Inference NO_FORECAST -------------------------------------------------

class _StubModel:
    model_id = "stub"
    version = "v1"
    def predict_one(self, features):
        return Prediction(score=0.42, horizon_days=5, model_id=self.model_id,
                          model_version=self.version, feature_set_hash="")


def test_inference_returns_no_forecast_when_no_production_model():
    reg = InMemoryModelRegistry()
    fs = FeatureSet(names=("ret_1d",))
    svc = InferenceService(reg, fs)
    bars = [_bar(date(2026, 1, i), 100 + i) for i in range(1, 4)]
    r = svc.score(instrument_id=IID_AAPL, as_of=datetime(2026, 1, 5, tzinfo=timezone.utc),
                  horizon_days=5, bars=bars)
    assert isinstance(r, NoForecast)
    assert r.reason is NoForecastReason.NO_PRODUCTION_MODEL


def test_inference_returns_forecast_when_production_exists():
    reg = InMemoryModelRegistry()
    fs = FeatureSet(names=("ret_1d",))
    rec = ModelRecord(
        model_id="m1", version="v1", market=Market.US, horizon_days=5,
        feature_set_hash=fs.content_hash, state=ModelState.DRAFT,
        created_at=datetime.now(timezone.utc), approved_by=None, approval_id=None,
    )
    reg.register(rec, artifact=_StubModel())
    reg.transition("m1", "v1", ModelState.CANDIDATE, actor="ops")
    reg.transition("m1", "v1", ModelState.PRODUCTION, actor="ops",
                   approval_id="APR-1", promotion_gate=_passing_gate())
    svc = InferenceService(reg, fs)
    bars = [_bar(date(2026, 1, i), 100 + i) for i in range(1, 6)]
    r = svc.score(instrument_id=IID_AAPL, as_of=datetime(2026, 1, 10, tzinfo=timezone.utc),
                  horizon_days=5, bars=bars)
    assert isinstance(r, Forecast)
    assert r.score == pytest.approx(0.42)


# ---- Risk engine -----------------------------------------------------------

def test_risk_rejects_without_permission():
    ctx = RiskContext(
        instrument_id=IID_AAPL, trade_date=date(2026, 6, 1),
        side=1, quantity=100, ref_price=100.0,
        user_permissions=frozenset(),  # empty -> deny
    )
    trace = default_engine().evaluate(ctx)
    assert default_engine().final_verdict(trace) is RiskVerdict.REJECT
    assert trace[0].code == "PERM_DENIED"


def test_risk_rejects_cn_short():
    ctx = RiskContext(
        instrument_id=IID_MOUTAI, trade_date=date(2026, 6, 1),
        side=-1, quantity=100, ref_price=1800.0,
        user_permissions=frozenset({"trade:CN"}),
    )
    trace = default_engine().evaluate(ctx)
    assert any(d.code == "NO_SHORT_CN" for d in trace)


def test_risk_rejects_at_cn_upper_limit():
    ctx = RiskContext(
        instrument_id=IID_MOUTAI, trade_date=date(2026, 6, 1),
        side=1, quantity=100, ref_price=110.0, prev_close=100.0,
        user_permissions=frozenset({"trade:CN"}),
    )
    trace = default_engine().evaluate(ctx)
    codes = [d.code for d in trace]
    assert "AT_UPPER_LIMIT" in codes


def test_risk_accepts_normal_us_buy():
    ctx = RiskContext(
        instrument_id=IID_AAPL, trade_date=date(2026, 6, 1),
        side=1, quantity=100, ref_price=100.0,
        user_permissions=frozenset({"trade:US"}),
    )
    trace = default_engine().evaluate(ctx)
    assert default_engine().final_verdict(trace) is RiskVerdict.ACCEPT


# ---- Portfolio -------------------------------------------------------------

def test_portfolio_long_only_topk_and_clip():
    fcs = [
        Forecast(IID_AAPL, datetime.now(timezone.utc), 5, +0.9, "m", "v", "h"),
        Forecast(IID_MOUTAI, datetime.now(timezone.utc), 5, +0.1, "m", "v", "h"),
    ]
    tgt = build_portfolio(fcs, PortfolioConfig(max_name_weight=0.5, gross_cap=1.0))
    assert tgt.gross <= 1.0 + 1e-9
    assert all(w <= 0.5 + 1e-9 for w in tgt.weights.values())
    assert all(w >= 0 for w in tgt.weights.values())


def test_portfolio_empty_when_all_below_min_score():
    fcs = [Forecast(IID_AAPL, datetime.now(timezone.utc), 5, -0.1, "m", "v", "h")]
    tgt = build_portfolio(fcs, PortfolioConfig(long_only=True, min_score=0.0))
    assert tgt.weights == {}
    assert tgt.cash == 1.0


# ---- Backtest & metrics ----------------------------------------------------

def test_backtest_zero_signal_produces_no_trades():
    bars = [_bar(date(2026, 1, i), 100 + i) for i in range(1, 11)]
    res = run_daily_signal_backtest(bars, lambda w: 0, cfg=BacktestConfig())
    assert res.n_trades == 0


def test_sharpe_and_mdd():
    eq = [100, 101, 99, 102, 105]
    r = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    s = sharpe_ratio(r, periods_per_year=252)
    assert isinstance(s, float)
    assert max_drawdown(eq) < 0


def test_information_coefficient_perfect_rank():
    ic = information_coefficient([1, 2, 3, 4], [10, 20, 30, 40])
    assert ic == pytest.approx(1.0)


def test_brier_and_isotonic():
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    out = isotonic_calibrate([0.1, 0.4, 0.3, 0.9], [0, 1, 0, 1])
    assert len(out) == 4
    # Monotone non-decreasing calibrated values
    ys = [v for _, v in out]
    assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))


def test_hit_rate():
    assert hit_rate([0.1, -0.2, 0.3, 0.0]) == pytest.approx(2 / 3)


# ---- Reporting -------------------------------------------------------------

def test_report_renders_forecast_and_no_forecast():
    f = Forecast(IID_AAPL, datetime(2026, 6, 1, tzinfo=timezone.utc), 5, 0.7, "m", "v", "hash")
    nf = NoForecast(IID_MOUTAI, datetime(2026, 6, 1, tzinfo=timezone.utc),
                    NoForecastReason.MISSING_FEATURE, "ret_1d missing")
    tgt = build_portfolio([f], PortfolioConfig())
    md = render_daily_report(as_of=datetime(2026, 6, 1, tzinfo=timezone.utc),
                             forecasts=[f], no_forecasts=[nf], portfolio=tgt,
                             metrics={"sharpe": 1.2})
    assert "AAPL" in md
    assert "missing_feature" in md
    assert "sharpe" in md


def test_render_risk_trace():
    ctx = RiskContext(instrument_id=IID_AAPL, trade_date=date(2026, 6, 1),
                      side=1, quantity=100, ref_price=100.0,
                      user_permissions=frozenset({"trade:US"}))
    trace = default_engine().evaluate(ctx)
    md = render_risk_trace(trace)
    assert "| accept |" in md or "| review |" in md


def test_render_backtest_summary():
    md = render_backtest_summary(total_return=0.12, sharpe=1.4, mdd=-0.08,
                                 hit_rate=0.55, n_trades=42)
    assert "1.40" in md and "-8.00%" in md


# ---- Admin MCP tools -------------------------------------------------------

def test_admin_register_and_promote_flow():
    from apps import __init__  # noqa
    import importlib
    admin_mod = importlib.import_module("apps.quant-admin-mcp.tools".replace("-", "_")) \
        if False else None
    # Use direct import via file path is awkward; import via package works because
    # apps directory uses hyphens. Fallback: import from adjusted path.
    import sys, pathlib
    admin_dir = pathlib.Path(__file__).resolve().parents[2] / "apps" / "quant-admin-mcp"
    sys.path.insert(0, str(admin_dir))
    try:
        import tools as admin_tools  # type: ignore
    finally:
        sys.path.pop(0)

    reg = InMemoryModelRegistry()
    audit = AuditLog(InMemoryAuditSink())
    tools = admin_tools.AdminTools(registry=reg, audit=audit)

    r1 = tools.register_model(
        model_id="m1", version="v1", market="US", horizon_days=5,
        feature_set_hash="abc", actor="alice",
    )
    assert r1["ok"] is True

    # Promotion to PRODUCTION without approval_id -> failure
    tools.promote_model(model_id="m1", version="v1", to_state="CANDIDATE", actor="alice")
    _cm = {"ic": 0.10, "net_return": 0.08}
    _bm = {"BuyAndHold": {"ic": 0.02, "net_return": 0.03}}
    bad = tools.promote_model(model_id="m1", version="v1", to_state="PRODUCTION",
                              actor="alice", candidate_metrics=_cm,
                              baseline_metrics=_bm)
    assert bad["ok"] is False

    good = tools.promote_model(model_id="m1", version="v1", to_state="PRODUCTION",
                               actor="alice", approval_id="APR-1",
                               candidate_metrics=_cm, baseline_metrics=_bm)
    assert good["ok"] is True
    assert reg.get_production(Market.US, 5) is not None
