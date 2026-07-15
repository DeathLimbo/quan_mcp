from datetime import date, datetime, timezone
from decimal import Decimal

from packages.common import AssetType, InstrumentId, Market, Venue
from packages.corporate_actions import AdjustMode, apply_adjustment
from packages.data_sources.contracts import Bar, CorporateAction


IID = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")


def _bar(d: date, close: Decimal) -> Bar:
    ts = datetime(d.year, d.month, d.day, 20, 0, tzinfo=timezone.utc)
    return Bar(
        instrument_id=IID,
        event_time_utc=ts,
        market_local_date=d,
        open=close, high=close, low=close, close=close,
        volume=Decimal("100"), turnover=None,
        adj_factor=Decimal("1"),
        available_at_utc=ts,
        source="test",
        calendar_version="v0", rule_version="v0",
    )


def test_split_2_for_1_backward_adjust():
    """After a 2-for-1 split, past prices should be halved; today's price unchanged."""
    bars = [_bar(date(2026, 1, 5), Decimal("100")),
            _bar(date(2026, 1, 6), Decimal("50"))]  # post-split
    action = CorporateAction(
        instrument_id=IID, action_type="SPLIT",
        announcement_date_utc=datetime(2026, 1, 4, tzinfo=timezone.utc),
        ex_date_local=date(2026, 1, 6),
        payable_date_local=None,
        ratio=Decimal("2"), currency=None, source="test",
        available_at_utc=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )
    out = apply_adjustment(bars, [action], mode=AdjustMode.BACKWARD)
    # Bar[0] (pre-split) close should be halved: 50
    assert out[0].close == Decimal("50.0000")
    # Bar[1] (on/after ex) close unchanged: 50
    assert out[1].close == Decimal("50.0000")
    # adj_factor multiplied on the earlier bar
    assert out[0].adj_factor == Decimal("0.5")
    assert out[1].adj_factor == Decimal("1")


def test_no_actions_returns_copy():
    bars = [_bar(date(2026, 1, 5), Decimal("100"))]
    out = apply_adjustment(bars, [], mode=AdjustMode.BACKWARD)
    assert out == bars
    assert out is not bars


def test_dividend_backward_adjust():
    bars = [_bar(date(2026, 1, 5), Decimal("100")),
            _bar(date(2026, 1, 6), Decimal("99"))]
    action = CorporateAction(
        instrument_id=IID, action_type="DIVIDEND",
        announcement_date_utc=datetime(2026, 1, 4, tzinfo=timezone.utc),
        ex_date_local=date(2026, 1, 6),
        payable_date_local=None,
        ratio=Decimal("1"),  # $1 dividend
        currency="USD", source="test",
        available_at_utc=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )
    out = apply_adjustment(bars, [action], mode=AdjustMode.BACKWARD)
    # factor = (100 - 1)/100 = 0.99 → pre-ex bar close = 100*0.99 = 99.0000
    assert out[0].close == Decimal("99.0000")
    assert out[1].close == Decimal("99.0000")  # today unchanged
