"""Cross-currency FX tests (tasks #45-#49).

Covers the spec §12.6 / §29 / §38 cross-currency chain:
  - FxConverter convert / fx_return (fail-closed, inverse fallback)
  - base_currency_returns (§38 模型: 评价扣除 FX 后收益)
  - attribute_exposures / currency_exposure (§29 仓位本币/基准市值)
  - render_daily_report degraded header (§38 系统: 部分市场失败明确降级)
  - InferenceService Forecast decomposition (§12.6 local/fx/base + §38 留痕)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import packages.features.basics  # noqa: F401  register default features
from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue,
)
from packages.data_sources.contracts import Bar
from packages.evaluation.metrics import (
    base_currency_returns, information_coefficient,
)
from packages.features.featureset import FeatureSet
from packages.fx.converter import FxConverter, FxNotAvailableError
from packages.inference.service import (
    Forecast, InferenceService, NoForecastReason,
)
from packages.models.base import Model, Prediction
from packages.models.registry import (
    InMemoryModelRegistry, ModelState, ModelRecord,
)
from packages.portfolio.builder import (
    PortfolioConfig, PortfolioTarget, attribute_exposures,
    build_portfolio, currency_exposure,
)
from packages.reporting.render import render_daily_report

IID_CN = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")
IID_US = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")
FS = FeatureSet(names=("ret_1d", "ret_5d", "vol_20d"))


# ---- stub FX rate provider --------------------------------------------------

_RATES = {  # base, quote -> {date -> rate}
    ("USD", "CNY"): {
        date(2026, 4, 25): Decimal("7.10"),
        date(2026, 4, 26): Decimal("7.10"),
        date(2026, 4, 30): Decimal("7.18"),
        date(2026, 5, 1): Decimal("7.20"),
    },
}


def _stub_provider(base: str, quote: str, on_or_before: date):
    m = _RATES.get((base, quote))
    if m:
        ks = [k for k in m if k <= on_or_before]
        return m[max(ks)] if ks else None
    return None


def _fx() -> FxConverter:
    return FxConverter(base_ccy="CNY", rate_provider=_stub_provider)


# ---- 1. FxConverter.convert -------------------------------------------------

def test_convert_same_ccy_returns_amount():
    fx = _fx()
    amt = Decimal("100")
    assert fx.convert(amt, from_ccy="CNY", to_ccy="CNY",
                      on_or_before=date(2026, 5, 1)) == amt


def test_convert_cross_ccy_uses_rate():
    fx = _fx()
    # 1 USD = 7.20 CNY on 2026-05-01
    out = fx.convert(Decimal("100"), from_ccy="USD", to_ccy="CNY",
                     on_or_before=date(2026, 5, 1))
    assert out == Decimal("720")


def test_convert_inverse_pair_fallback():
    fx = _fx()
    # no CNY/USD row stored → converter inverts USD/CNY
    out = fx.convert(Decimal("720"), from_ccy="CNY", to_ccy="USD",
                     on_or_before=date(2026, 5, 1))
    assert out == Decimal("100")


def test_convert_missing_rate_raises():
    fx = _fx()
    # date before any rate → fail-closed
    with pytest.raises(FxNotAvailableError):
        fx.convert(Decimal("100"), from_ccy="USD", to_ccy="CNY",
                   on_or_before=date(2020, 1, 1))


def test_convert_no_provider_raises():
    fx = FxConverter(base_ccy="CNY", rate_provider=None)
    with pytest.raises(FxNotAvailableError):
        fx.convert(Decimal("1"), from_ccy="USD", to_ccy="CNY",
                   on_or_before=date(2026, 5, 1))


# ---- 2. FxConverter.fx_return (§12.6 attribution) --------------------------

def test_fx_return_same_ccy_is_zero():
    assert _fx().fx_return(local_ccy="CNY",
                           start=date(2026, 4, 25), end=date(2026, 5, 1)) == Decimal("0")


def test_fx_return_cross_ccy_realised():
    # USD/CNY 7.10 → 7.20 over the window
    r = _fx().fx_return(local_ccy="USD",
                        start=date(2026, 4, 25), end=date(2026, 5, 1))
    assert abs(float(r) - (7.20 - 7.10) / 7.10) < 1e-9


def test_fx_return_missing_raises():
    with pytest.raises(FxNotAvailableError):
        _fx().fx_return(local_ccy="USD",
                        start=date(2019, 1, 1), end=date(2019, 6, 1))


# ---- 3. base_currency_returns (§38 模型: 扣除 FX 后评价) --------------------

def test_base_currency_returns_mixed():
    fx = _fx()
    # CN local=CNY→base unchanged; US local=USD→ add realised FX
    local = [0.01, 0.02]
    ccys = ["CNY", "USD"]
    ends = [date(2026, 5, 1), date(2026, 5, 1)]
    base = base_currency_returns(local, ccys, ends, fx, horizon_days=5)
    # CN: 0.01; US: 0.02 + (7.20-7.10)/7.10
    assert abs(base[0] - 0.01) < 1e-12
    assert abs(base[1] - (0.02 + (7.20 - 7.10) / 7.10)) < 1e-9


def test_base_currency_returns_ic_pairs_with_information_coefficient():
    fx = _fx()
    scores = [0.5, -0.5]
    local = [0.01, -0.02]
    ccys = ["CNY", "USD"]
    ends = [date(2026, 5, 1), date(2026, 5, 1)]
    base = base_currency_returns(local, ccys, ends, fx, horizon_days=5)
    ic = information_coefficient(scores, base)
    assert -1.0 <= ic <= 1.0  # well-defined


# ---- 4. attribute_exposures / currency_exposure (§29) ----------------------

def test_attribute_exposures_mixed_ccy():
    fx = _fx()
    weights = {IID_CN: 0.6, IID_US: 0.3}
    ccy_map = {IID_CN: "CNY", IID_US: "USD"}
    exps = attribute_exposures(weights, ccy_map=ccy_map, fx_converter=fx,
                               as_of=date(2026, 5, 1), equity=10000.0)
    by_iid = {e.instrument_id: e for e in exps}
    # CN: same ccy, local==base==6000, fx_exposure=0
    cn = by_iid[IID_CN]
    assert cn.local_market_value == 6000.0
    assert cn.base_market_value == 6000.0
    assert cn.fx_exposure == 0.0
    # US: base=3000, local=3000/7.20 USD, fx_exposure=3000
    us = by_iid[IID_US]
    assert us.base_market_value == 3000.0
    assert abs(us.local_market_value - 3000.0 / 7.20) < 1e-6
    assert us.fx_exposure == 3000.0


def test_currency_exposure_aggregates_by_ccy():
    fx = _fx()
    weights = {IID_CN: 0.6, IID_US: 0.3}
    ccy_map = {IID_CN: "CNY", IID_US: "USD"}
    agg = currency_exposure(weights, ccy_map=ccy_map, fx_converter=fx,
                            as_of=date(2026, 5, 1))
    assert abs(agg["CNY"] - 0.6) < 1e-12
    assert abs(agg["USD"] - 0.3) < 1e-12


# ---- 5. render_daily_report degraded header (§38 系统) ---------------------

def test_render_marks_degraded_when_no_forecasts():
    from packages.inference.service import NoForecast
    nf = NoForecast(instrument_id=IID_US, as_of=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    reason=NoForecastReason.NO_PRODUCTION_MODEL, detail="none")
    port = PortfolioTarget(weights={IID_CN: 0.8}, cash=0.2)
    md = render_daily_report(
        as_of=datetime(2026, 5, 1, 22, tzinfo=timezone.utc),
        forecasts=[], no_forecasts=[nf], portfolio=port,
    )
    assert "DEGRADED" in md
    assert IID_US.canonical() in md


def test_render_clean_when_all_forecasts():
    fc = Forecast(
        instrument_id=IID_CN, as_of=datetime(2026, 5, 1, tzinfo=timezone.utc),
        horizon_days=5, score=0.01, model_id="cn_v1", model_version="v1",
        feature_hash="abc", expected_return_local=0.01,
        expected_fx_return=None, expected_return_base=0.01,
    )
    port = PortfolioTarget(weights={IID_CN: 0.8}, cash=0.2)
    md = render_daily_report(
        as_of=datetime(2026, 5, 1, 22, tzinfo=timezone.utc),
        forecasts=[fc], no_forecasts=[], portfolio=port,
    )
    assert "DEGRADED" not in md


# ---- 6. InferenceService Forecast decomposition (§12.6 + §38 留痕) ----------

class _StubModel(Model):
    def __init__(self, model_id: str, score: float) -> None:
        self.model_id = model_id
        self.version = "v1"
        self._score = score

    def predict_one(self, features: dict) -> Prediction:
        return Prediction(
            score=self._score, horizon_days=5, model_id=self.model_id,
            model_version=self.version, feature_set_hash=FS.content_hash,
        )


def _register_prod(reg: InMemoryModelRegistry, market: Market,
                   model_id: str, score: float) -> None:
    rec = ModelRecord(
        model_id=model_id, version="v1", market=market, horizon_days=5,
        feature_set_hash=FS.content_hash, state=ModelState.DRAFT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        approved_by=None, approval_id=None,
    )
    reg.register(rec, artifact=_StubModel(model_id, score))
    reg.transition(model_id, "v1", ModelState.CANDIDATE, actor="ops")
    reg.transition(model_id, "v1", ModelState.PRODUCTION,
                   actor="ops", approval_id=f"APR-{model_id}",
                   promotion_gate=SimpleNamespace(passed=True, losses=()))


def _make_bars(iid: InstrumentId, n: int = 30) -> list[Bar]:
    """n synthetic bars with rising closes so ret/vol features resolve."""
    start = date(2026, 1, 5)
    bars = []
    for i in range(n):
        d = start + timedelta(days=i)
        close = Decimal(100 + i)  # 100, 101, 102, ...
        ev = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
        bars.append(Bar(
            instrument_id=iid, event_time_utc=ev, market_local_date=d,
            open=close, high=close, low=close, close=close,
            volume=Decimal("1000"), turnover=None, adj_factor=Decimal("1"),
            available_at_utc=ev, source="fake",
            calendar_version="us.v0", rule_version="us.v0",
            source_version="yfinance.v1",
        ))
    return bars


def test_forecast_decomposition_cross_ccy():
    reg = InMemoryModelRegistry()
    _register_prod(reg, Market.US, "us_v1", score=0.03)
    fx = _fx()
    svc = InferenceService(reg, FS, fx_converter=fx, base_ccy="CNY")
    as_of = datetime(2026, 5, 1, 22, tzinfo=timezone.utc)
    fc = svc.score(instrument_id=IID_US, as_of=as_of, horizon_days=5,
                   bars=_make_bars(IID_US), instrument_ccy="USD")
    assert isinstance(fc, Forecast)
    # §12.6 decomposition
    assert fc.expected_return_local == 0.03
    assert fc.expected_fx_return is not None  # USD→CNY realised over 5d
    assert abs(fc.expected_fx_return - (7.20 - 7.10) / 7.10) < 1e-9
    assert abs(fc.expected_return_base - (0.03 + fc.expected_fx_return)) < 1e-9
    # §38 留痕: data / calendar / rule version stamped from bars
    assert fc.data_version == "yfinance.v1"
    assert fc.calendar_version == "us.v0"
    assert fc.rule_version == "us.v0"


def test_forecast_decomposition_same_ccy_no_fx():
    reg = InMemoryModelRegistry()
    _register_prod(reg, Market.CN, "cn_v1", score=0.02)
    fx = _fx()
    svc = InferenceService(reg, FS, fx_converter=fx, base_ccy="CNY")
    as_of = datetime(2026, 5, 1, 22, tzinfo=timezone.utc)
    fc = svc.score(instrument_id=IID_CN, as_of=as_of, horizon_days=5,
                   bars=_make_bars(IID_CN), instrument_ccy="CNY")
    assert isinstance(fc, Forecast)
    assert fc.expected_return_local == 0.02
    assert fc.expected_fx_return is None  # same ccy → no FX attribution
    assert fc.expected_return_base == 0.02


def test_forecast_decomposition_no_converter_backward_compat():
    reg = InMemoryModelRegistry()
    _register_prod(reg, Market.US, "us_v1", score=0.03)
    svc = InferenceService(reg, FS)  # no fx_converter
    as_of = datetime(2026, 5, 1, 22, tzinfo=timezone.utc)
    fc = svc.score(instrument_id=IID_US, as_of=as_of, horizon_days=5,
                   bars=_make_bars(IID_US), instrument_ccy="USD")
    assert isinstance(fc, Forecast)
    # no converter → no FX attribution, base==local (fail-closed)
    assert fc.expected_return_local == 0.03
    assert fc.expected_fx_return is None
    assert fc.expected_return_base == 0.03
