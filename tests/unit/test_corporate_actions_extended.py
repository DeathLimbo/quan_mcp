from datetime import date, datetime, timezone
from decimal import Decimal

from packages.common import AssetType, InstrumentId, Market, Venue
from packages.corporate_actions import AdjustMode, apply_adjustment
from packages.data_sources.contracts import Bar, CorporateAction


IID = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "TEST")


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


def _action(kind: str, ex: date, ratio: Decimal) -> CorporateAction:
    ann = datetime(ex.year, ex.month, ex.day - 1, tzinfo=timezone.utc)
    return CorporateAction(
        instrument_id=IID, action_type=kind,
        announcement_date_utc=ann,
        ex_date_local=ex,
        payable_date_local=None,
        ratio=ratio,
        currency="USD" if kind in ("DIVIDEND", "SPINOFF") else None,
        source="test", available_at_utc=ann,
    )


# -----------------------------------------------------------------
# SPINOFF: treat spun-off value as in-kind dividend.
# -----------------------------------------------------------------
def test_spinoff_backward_adjust_shrinks_past_prices():
    """Spun-off value $5/share, parent trades at $100 cum, $95 ex → past bars scale by 0.95."""
    bars = [_bar(date(2026, 2, 5), Decimal("100")),
            _bar(date(2026, 2, 6), Decimal("95"))]
    a = _action("SPINOFF", ex=date(2026, 2, 6), ratio=Decimal("5"))
    out = apply_adjustment(bars, [a], mode=AdjustMode.BACKWARD)
    # factor = (100 - 5)/100 = 0.95
    assert out[0].close == Decimal("95.0000")
    assert out[1].close == Decimal("95.0000")
    assert out[0].adj_factor == Decimal("0.95")


def test_spinoff_ignores_when_value_gte_close():
    bars = [_bar(date(2026, 2, 5), Decimal("100")),
            _bar(date(2026, 2, 6), Decimal("50"))]
    a = _action("SPINOFF", ex=date(2026, 2, 6), ratio=Decimal("120"))
    out = apply_adjustment(bars, [a], mode=AdjustMode.BACKWARD)
    # Value exceeds cum close → refuse to invert prices; identity factor.
    assert out[0].close == Decimal("100")
    assert out[1].close == Decimal("50")


# -----------------------------------------------------------------
# RIGHTS: ratio pre-computed as TERP / cum-rights close.
# -----------------------------------------------------------------
def test_rights_backward_adjust_uses_ratio_directly():
    bars = [_bar(date(2026, 3, 5), Decimal("100")),
            _bar(date(2026, 3, 6), Decimal("90"))]
    # TERP/cum = 0.9  →  past bars multiplied by 0.9
    a = _action("RIGHTS", ex=date(2026, 3, 6), ratio=Decimal("0.9"))
    out = apply_adjustment(bars, [a], mode=AdjustMode.BACKWARD)
    assert out[0].close == Decimal("90.0000")
    assert out[1].close == Decimal("90.0000")
    assert out[0].adj_factor == Decimal("0.9")


def test_rights_invalid_ratio_is_identity():
    bars = [_bar(date(2026, 3, 5), Decimal("100")),
            _bar(date(2026, 3, 6), Decimal("90"))]
    a = _action("RIGHTS", ex=date(2026, 3, 6), ratio=Decimal("0"))
    out = apply_adjustment(bars, [a], mode=AdjustMode.BACKWARD)
    assert out[0].close == Decimal("100")
    assert out[1].close == Decimal("90")


# -----------------------------------------------------------------
# MERGER: stock-for-stock; ratio = new_per_old.
# -----------------------------------------------------------------
def test_merger_1p5_new_shares_per_old():
    """Post-merger holders receive 1.5 new shares per old share; past prices
    divided by 1.5 to keep the new-share basis continuous."""
    bars = [_bar(date(2026, 4, 5), Decimal("150")),
            _bar(date(2026, 4, 6), Decimal("100"))]
    a = _action("MERGER", ex=date(2026, 4, 6), ratio=Decimal("1.5"))
    out = apply_adjustment(bars, [a], mode=AdjustMode.BACKWARD)
    # factor = 1/1.5 ~ 0.6666...  → 150 * 0.6667 ≈ 100
    assert out[1].close == Decimal("100")  # unchanged
    assert abs(out[0].close - Decimal("100")) < Decimal("0.01")


def test_merger_invalid_ratio_is_identity():
    bars = [_bar(date(2026, 4, 5), Decimal("100")),
            _bar(date(2026, 4, 6), Decimal("100"))]
    a = _action("MERGER", ex=date(2026, 4, 6), ratio=Decimal("-1"))
    out = apply_adjustment(bars, [a], mode=AdjustMode.BACKWARD)
    assert out[0].close == Decimal("100")


# -----------------------------------------------------------------
# Composed sequence: SPLIT then DIVIDEND then SPINOFF, chronological.
# -----------------------------------------------------------------
def test_multi_action_chain_composes_factors():
    bars = [_bar(date(2026, 5, 1), Decimal("200")),
            _bar(date(2026, 5, 5), Decimal("100")),   # after 2-for-1
            _bar(date(2026, 5, 10), Decimal("99")),   # after $1 div
            _bar(date(2026, 5, 15), Decimal("94"))]   # after $5 spinoff
    actions = [
        _action("SPLIT",    ex=date(2026, 5, 5),  ratio=Decimal("2")),
        _action("DIVIDEND", ex=date(2026, 5, 10), ratio=Decimal("1")),
        _action("SPINOFF",  ex=date(2026, 5, 15), ratio=Decimal("5")),
    ]
    out = apply_adjustment(bars, actions, mode=AdjustMode.BACKWARD)
    # Cumulative factor at bars[0] = 0.5 * (99/100) * (94/99) = 0.5 * 0.94 = 0.47
    assert abs(out[0].close - Decimal("94")) < Decimal("0.01")
    assert out[-1].close == Decimal("94")  # anchor unchanged
