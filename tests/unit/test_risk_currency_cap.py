"""Risk Engine per-currency cap tests (task #52, spec §29 币种上限)."""
from __future__ import annotations

from datetime import date

from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.risk.engine import RiskContext, RiskEngine, RiskVerdict

IID_US = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")


def _ctx(*, per_ccy_current: float, per_ccy_limit: float = 0.40) -> RiskContext:
    return RiskContext(
        instrument_id=IID_US,
        trade_date=date(2026, 5, 1),
        side=1,
        quantity=100,
        ref_price=150.0,
        user_permissions=frozenset({"trade:US"}),
        per_ccy_exposure_frac_current=per_ccy_current,
        per_ccy_exposure_limit=per_ccy_limit,
    )


def test_currency_cap_rejects_over_limit():
    trace = RiskEngine().evaluate(_ctx(per_ccy_current=0.55))
    assert RiskEngine().final_verdict(trace) is RiskVerdict.REJECT
    rejecting = [d for d in trace if d.verdict is RiskVerdict.REJECT]
    assert any(d.code == "CCY_LIMIT" for d in rejecting), \
        f"expected CCY_LIMIT reject, got {[d.code for d in rejecting]}"


def test_currency_cap_accepts_within_limit():
    trace = RiskEngine().evaluate(_ctx(per_ccy_current=0.30))
    assert RiskEngine().final_verdict(trace) is RiskVerdict.ACCEPT


def test_currency_cap_default_limit_40pct():
    # default per_ccy_exposure_limit == 0.40 (§29)
    ctx = _ctx(per_ccy_current=0.40)
    assert RiskEngine().final_verdict(RiskEngine().evaluate(ctx)) is RiskVerdict.ACCEPT
    ctx_over = _ctx(per_ccy_current=0.41)
    assert RiskEngine().final_verdict(RiskEngine().evaluate(ctx_over)) is RiskVerdict.REJECT
