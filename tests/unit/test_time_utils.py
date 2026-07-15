"""DST and UTC-first correctness tests."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from packages.common import MarketClock, Market, ensure_utc, to_utc


def test_ensure_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 3, 8, 9, 30))


def test_us_market_dst_spring_forward():
    """US DST starts 2026-03-08. Before: EST = UTC-5. After: EDT = UTC-4."""
    clock = MarketClock(Market.US)
    # 09:30 local on 2026-03-07 (still EST) => 14:30 UTC
    before = datetime(2026, 3, 7, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert to_utc(before).hour == 14
    # 09:30 local on 2026-03-09 (EDT) => 13:30 UTC
    after = datetime(2026, 3, 9, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert to_utc(after).hour == 13
    assert clock.tz.key == "America/New_York"


def test_us_market_dst_fall_back():
    """US DST ends 2026-11-01. Before: EDT = UTC-4. After: EST = UTC-5."""
    edt = datetime(2026, 10, 31, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    est = datetime(2026, 11, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert to_utc(edt).hour == 13
    assert to_utc(est).hour == 14


def test_cn_no_dst():
    """China does not observe DST; offset is always +08:00."""
    a = datetime(2026, 3, 8, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    b = datetime(2026, 11, 1, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert to_utc(a).hour == 1
    assert to_utc(b).hour == 1


def test_market_clock_is_regular_session():
    us = MarketClock(Market.US)
    # 10:00 EST -> 15:00 UTC on a non-DST winter day
    dt = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)
    assert us.is_regular_session(dt)
    # 07:00 EST is pre-market
    dt2 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert not us.is_regular_session(dt2)


def test_to_utc_assume_tz():
    naive = datetime(2026, 6, 1, 12, 0)
    out = to_utc(naive, assume_tz=ZoneInfo("America/New_York"))
    # June -> EDT -> 12:00 EDT = 16:00 UTC
    assert out.hour == 16
    assert out.tzinfo is timezone.utc
