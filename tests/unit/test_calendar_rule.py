from datetime import date

import pytest

from packages.calendar_rule import (
    calendar_version, get_calendar, get_price_limit, rule_version,
)
from packages.common import AssetType, InstrumentId, Market, Venue


def test_versions_are_strings():
    assert isinstance(calendar_version(), str)
    assert isinstance(rule_version(), str)


def test_cn_calendar_skips_weekends_and_holidays():
    cal = get_calendar(Market.CN)
    # 2026-01-01 is New Year, in seed holiday set
    assert not cal.is_session(date(2026, 1, 1))
    # 2026-01-03 is Saturday
    assert not cal.is_session(date(2026, 1, 3))
    # 2026-01-05 is Monday and not a holiday
    assert cal.is_session(date(2026, 1, 5))
    ns = cal.next_session(date(2026, 1, 1))
    assert ns >= date(2026, 1, 2)
    assert cal.is_session(ns)


def test_us_calendar_thanksgiving():
    cal = get_calendar(Market.US)
    assert not cal.is_session(date(2026, 11, 26))  # Thanksgiving
    assert cal.is_session(date(2026, 11, 25))


def test_sessions_between_dedup_and_ordered():
    cal = get_calendar(Market.CN)
    sess = cal.sessions_between(date(2026, 1, 5), date(2026, 1, 9))
    assert sess == [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7),
                    date(2026, 1, 8), date(2026, 1, 9)]


def test_price_limit_us_no_daily_band():
    aapl = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")
    lim = get_price_limit(aapl)
    assert lim.up_pct is None and lim.down_pct is None


def test_price_limit_cn_main_vs_star_vs_st():
    main = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")
    assert get_price_limit(main).up_pct == __import__("decimal").Decimal("0.10")
    star = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "688981")
    assert get_price_limit(star).up_pct == __import__("decimal").Decimal("0.20")
    st = InstrumentId(Market.CN, Venue.SSE, AssetType.EQUITY, "600519")
    assert get_price_limit(st, name_local="*ST 示例").up_pct == __import__("decimal").Decimal("0.05")


def test_price_limit_bse():
    bse = InstrumentId(Market.CN, Venue.BSE, AssetType.EQUITY, "830799")
    lim = get_price_limit(bse)
    assert lim.up_pct == __import__("decimal").Decimal("0.30")
