"""Unit tests for the Phase-2 event-driven execution models.

Covers CN T+1 + up/down-limit lock, US spread+slippage, Fund cutoff +
min-holding + redemption fee brackets.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.contracts import Bar
from packages.backtest.execution import (
    CnExecutionModel,
    Fill,
    FundExecutionModel,
    NoFill,
    OrderIntent,
    Side,
    UsExecutionModel,
)


def _bar(iid, d, o, c, *, av=None):
    dt = datetime(d.year, d.month, d.day, 8, 0, tzinfo=timezone.utc)
    return Bar(
        instrument_id=iid,
        event_time_utc=dt, market_local_date=d,
        open=Decimal(str(o)), high=Decimal(str(max(o, c))),
        low=Decimal(str(min(o, c))), close=Decimal(str(c)),
        volume=Decimal("1000"), turnover=None, adj_factor=Decimal("1"),
        available_at_utc=av or dt, source="test",
        calendar_version="v0", rule_version="v0",
    )


IID_MOUTAI = InstrumentId(market=Market.CN, venue=Venue.SSE, asset_type=AssetType.EQUITY, symbol="600519")
IID_AAPL = InstrumentId(market=Market.US, venue=Venue.NASDAQ, asset_type=AssetType.EQUITY, symbol="AAPL")
IID_FUND = InstrumentId(market=Market.CN, venue=Venue.CN_FUND, asset_type=AssetType.FUND, symbol="005827")


# --- CN ---------------------------------------------------------------

def test_cn_buy_fills_at_next_open_with_slippage():
    bars = [_bar(IID_MOUTAI, date(2026, 7, 13), 100, 100),
            _bar(IID_MOUTAI, date(2026, 7, 14), 101, 102)]
    m = CnExecutionModel()
    order = OrderIntent(instrument_id=IID_MOUTAI, side=Side.BUY,
                        quantity=Decimal("100"), decision_date=date(2026, 7, 13))
    f = m.execute(order, bars=bars)
    assert isinstance(f, Fill)
    # slippage_bps=5 → +0.05% on open 101
    assert f.fill_price > Decimal("101")
    # fees: commission 2.5 + transfer 0.1 = 2.6 bps of notional
    assert f.fees > 0 and f.cash_delta < 0


def test_cn_up_limit_lock_blocks_buy():
    # prior close 100, next open 110 (+10%) → up-limit
    bars = [_bar(IID_MOUTAI, date(2026, 7, 13), 100, 100),
            _bar(IID_MOUTAI, date(2026, 7, 14), 110, 110)]
    m = CnExecutionModel()
    order = OrderIntent(instrument_id=IID_MOUTAI, side=Side.BUY,
                        quantity=Decimal("100"), decision_date=date(2026, 7, 13))
    r = m.execute(order, bars=bars)
    assert isinstance(r, NoFill) and r.reason == "UP_LIMIT_LOCK"


def test_cn_halted_returns_nofill():
    bars = [_bar(IID_MOUTAI, date(2026, 7, 13), 100, 100),
            _bar(IID_MOUTAI, date(2026, 7, 14), 101, 102)]
    m = CnExecutionModel()
    r = m.execute(
        OrderIntent(instrument_id=IID_MOUTAI, side=Side.BUY,
                    quantity=Decimal("100"), decision_date=date(2026, 7, 13)),
        bars=bars, halted_dates=frozenset({date(2026, 7, 14)}),
    )
    assert isinstance(r, NoFill) and r.reason == "HALTED"


def test_cn_sell_stamp_tax_higher_than_buy():
    bars = [_bar(IID_MOUTAI, date(2026, 7, 13), 100, 100),
            _bar(IID_MOUTAI, date(2026, 7, 14), 100, 100)]
    m = CnExecutionModel()
    buy = m.execute(OrderIntent(instrument_id=IID_MOUTAI, side=Side.BUY,
                                quantity=Decimal("100"), decision_date=date(2026, 7, 13)),
                    bars=bars)
    sell = m.execute(OrderIntent(instrument_id=IID_MOUTAI, side=Side.SELL,
                                 quantity=Decimal("100"), decision_date=date(2026, 7, 13)),
                     bars=bars)
    assert sell.fees > buy.fees   # 10bps stamp tax added on sell


# --- US ---------------------------------------------------------------

def test_us_fills_at_next_open_with_spread():
    bars = [_bar(IID_AAPL, date(2026, 7, 13), 200, 200),
            _bar(IID_AAPL, date(2026, 7, 14), 201, 202)]
    m = UsExecutionModel()
    f = m.execute(OrderIntent(instrument_id=IID_AAPL, side=Side.BUY,
                              quantity=Decimal("10"), decision_date=date(2026, 7, 13)),
                  bars=bars)
    assert isinstance(f, Fill)
    assert f.fill_price > Decimal("201")   # half-spread + slippage


def test_us_delisted_no_fill():
    bars = [_bar(IID_AAPL, date(2026, 7, 13), 200, 200),
            _bar(IID_AAPL, date(2026, 7, 14), 201, 202)]
    m = UsExecutionModel()
    r = m.execute(OrderIntent(instrument_id=IID_AAPL, side=Side.BUY,
                              quantity=Decimal("10"), decision_date=date(2026, 7, 13)),
                  bars=bars, delisted_from=date(2026, 7, 14))
    assert isinstance(r, NoFill) and r.reason == "DELISTED"


# --- Fund -------------------------------------------------------------

def test_fund_subscription_fee_applied():
    bars = [_bar(IID_FUND, date(2026, 7, 13), 1.0, 1.0),
            _bar(IID_FUND, date(2026, 7, 14), 1.0, 1.0)]
    m = FundExecutionModel()
    f = m.execute(OrderIntent(instrument_id=IID_FUND, side=Side.BUY,
                              quantity=Decimal("1000"), decision_date=date(2026, 7, 13)),
                  bars=bars)
    assert isinstance(f, Fill)
    # 1000 * 1.0 * 1.2% = 12
    assert f.fees == Decimal("12.0000")


def test_fund_min_holding_violation():
    bars = [_bar(IID_FUND, date(2026, 7, 13), 1.0, 1.0),
            _bar(IID_FUND, date(2026, 7, 14), 1.0, 1.0)]
    m = FundExecutionModel(min_holding_days=7)
    r = m.execute(OrderIntent(instrument_id=IID_FUND, side=Side.SELL,
                              quantity=Decimal("100"), decision_date=date(2026, 7, 13)),
                  bars=bars, last_buy_date=date(2026, 7, 10))
    assert isinstance(r, NoFill) and r.reason == "MIN_HOLDING_VIOLATION"


def test_fund_redemption_fee_bracket_by_holding_days():
    bars = [_bar(IID_FUND, date(2026, 7, 13), 1.0, 1.0),
            _bar(IID_FUND, date(2026, 7, 14), 1.0, 1.0)]
    m = FundExecutionModel()
    # held < 7 days → 150bps
    r = m.execute(OrderIntent(instrument_id=IID_FUND, side=Side.SELL,
                              quantity=Decimal("1000"), decision_date=date(2026, 7, 13)),
                  bars=bars, last_buy_date=date(2026, 7, 10))
    assert isinstance(r, Fill)
    assert r.fees == Decimal("15.0000")   # 1000 * 1.0 * 1.5%


def test_fund_cutoff_shifts_to_next_day():
    bars = [_bar(IID_FUND, date(2026, 7, 13), 1.0, 1.0),
            _bar(IID_FUND, date(2026, 7, 14), 1.0, 1.0),
            _bar(IID_FUND, date(2026, 7, 15), 1.05, 1.05)]
    m = FundExecutionModel()
    late = datetime(2026, 7, 13, 16, 0)
    r = m.execute(OrderIntent(instrument_id=IID_FUND, side=Side.BUY,
                              quantity=Decimal("100"), decision_date=date(2026, 7, 13)),
                  bars=bars, decision_time_local=late)
    assert isinstance(r, Fill)
    assert r.fill_date == date(2026, 7, 15)  # post-cutoff shifted one extra day
