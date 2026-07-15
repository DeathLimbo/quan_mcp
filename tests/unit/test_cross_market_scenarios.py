"""Cross-market asymmetric-holiday + reverse-split scenarios (spec §7.4/§8).

The spec's DQ automation checklist explicitly calls out:

- 中国休市而美国开市、美国休市而中国开市 (asymmetric holidays)
- 股票拆分、反向拆分和现金分红日 (splits, reverse splits, cash dividends)

The regular Golden Dataset covers forward splits, dividends, and DST. This
module fills the two remaining gaps: asymmetric holidays via the trading
calendars, and reverse-split ratio semantics via ``apply_adjustment``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from packages.calendar_rule import get_calendar
from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue,
)
from packages.corporate_actions.adjust import AdjustMode, apply_adjustment
from packages.data_sources.contracts import Bar, CorporateAction


# ---------------------------------------------------------------------
# Cross-market asymmetric holidays.
# 2026-05-01 = CN Labour Day (CN closed) but US is open.
# 2026-11-26 = US Thanksgiving (US closed) but CN is open.
# ---------------------------------------------------------------------
def test_cn_labour_day_us_still_open():
    cn = get_calendar(Market.CN)
    us = get_calendar(Market.US)
    assert not cn.is_session(date(2026, 5, 1)), "CN closed on Labour Day"
    assert us.is_session(date(2026, 5, 1)), "US open on 2026-05-01 (Friday)"


def test_us_thanksgiving_cn_still_open():
    cn = get_calendar(Market.CN)
    us = get_calendar(Market.US)
    assert not us.is_session(date(2026, 11, 26)), "US closed on Thanksgiving"
    assert cn.is_session(date(2026, 11, 26)), "CN open on 2026-11-26 (Thursday)"


def test_cross_market_available_at_diverges_on_asymmetric_holiday():
    """On a CN-closed / US-open day, CN.next_session moves forward while US does not."""
    cn = get_calendar(Market.CN)
    us = get_calendar(Market.US)
    # From 2026-04-30 (Thu):
    cn_next = cn.next_session(date(2026, 4, 30))   # skips 05-01 (holiday) + 05-02/03 (weekend)
    us_next = us.next_session(date(2026, 4, 30))
    assert cn_next == date(2026, 5, 4)             # Monday
    assert us_next == date(2026, 5, 1)             # Friday
    # The datasets that mix CN + US on 2026-05-01 must therefore drop CN rows
    # for that date; the calendar contract makes the gap explicit rather than
    # silently interpolating.


# ---------------------------------------------------------------------
# Reverse split (ratio < 1).
# A 1-for-5 reverse split ex-date halves-then-some historical prices when
# backward-adjusted.
# ---------------------------------------------------------------------
def _reverse_split_bars(iid: InstrumentId) -> list[Bar]:
    """Two bars straddling a 2026-04-01 1-for-5 reverse split."""
    def _b(d: date, close: str) -> Bar:
        et = datetime(d.year, d.month, d.day, 20, tzinfo=timezone.utc)
        return Bar(
            instrument_id=iid, event_time_utc=et, market_local_date=d,
            open=Decimal(close), high=Decimal(close), low=Decimal(close),
            close=Decimal(close), volume=Decimal("1000"), turnover=None,
            adj_factor=Decimal("1"), available_at_utc=et,
            source="golden", calendar_version="v0", rule_version="v0",
        )
    return [_b(date(2026, 3, 31), "2.00"),   # pre-split
            _b(date(2026, 4, 1), "10.00")]   # post-split (5x higher)


def test_reverse_split_backward_adjust_scales_pre_ex_history():
    iid = InstrumentId(market=Market.US, venue=Venue.NASDAQ,
                       asset_type=AssetType.EQUITY, symbol="ABCD")
    bars = _reverse_split_bars(iid)
    # 1-for-5 reverse split: ratio=0.2 means each 5 old shares → 1 new share.
    action = CorporateAction(
        instrument_id=iid, action_type="SPLIT",
        announcement_date_utc=datetime(2026, 3, 15, tzinfo=timezone.utc),
        ex_date_local=date(2026, 4, 1), payable_date_local=None,
        ratio=Decimal("0.2"), currency="USD", source="golden",
        available_at_utc=datetime(2026, 3, 15, tzinfo=timezone.utc),
    )
    adjusted = apply_adjustment(bars, [action], mode=AdjustMode.BACKWARD)
    pre = next(b for b in adjusted if b.market_local_date == date(2026, 3, 31))
    post = next(b for b in adjusted if b.market_local_date == date(2026, 4, 1))
    # Pre-ex price scales by 1/ratio = 5 → 2.00 * 5 = 10.00 (matches post-ex).
    assert pre.close == Decimal("10.00")
    assert post.close == Decimal("10.00")
