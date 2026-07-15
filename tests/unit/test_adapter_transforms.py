"""Unit tests for adapter DataFrame → Bar transforms.

We do NOT depend on yfinance / akshare / pandas being installed. Both
adapters keep the network import strictly inside ``__init__`` and the
DataFrame contract used by ``_df_to_bars`` is a duck-typed iterrows
protocol, so a minimal fake suffices.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.adapters.akshare_adapter import AkshareAdapter
from packages.data_sources.adapters.yfinance_adapter import YFinanceAdapter


class _FakeDF:
    def __init__(self, rows, columns=None):
        # rows is list of (index, dict). columns is optional list.
        self._rows = list(rows)
        self.columns = columns or (list(rows[0][1].keys()) if rows else [])
        self.empty = not rows

    def iterrows(self):
        return iter(self._rows)


def _yf_adapter() -> YFinanceAdapter:
    ad = object.__new__(YFinanceAdapter)
    ad._eod_lag = timedelta(minutes=30)
    return ad


def _ak_adapter() -> AkshareAdapter:
    ad = object.__new__(AkshareAdapter)
    ad._eod_lag = timedelta(hours=1)
    return ad


def test_yfinance_df_to_bars_no_adjust() -> None:
    ad = _yf_adapter()
    iid = InstrumentId(market=Market.US, venue=Venue.NASDAQ,
                       asset_type=AssetType.EQUITY, symbol="AAPL")
    ts = datetime(2024, 1, 3, tzinfo=timezone.utc)
    df = _FakeDF([
        (ts, {"Open": "180.0", "High": "182.0", "Low": "179.0",
              "Close": "181.0", "Adj Close": "181.0", "Volume": 1_000_000}),
    ])
    bars = list(ad._df_to_bars(df, iid, adjust="none"))
    assert len(bars) == 1
    b = bars[0]
    assert b.market_local_date == date(2024, 1, 3)
    assert b.event_time_utc == datetime(2024, 1, 3, 20, 0, tzinfo=timezone.utc)
    assert b.available_at_utc == b.event_time_utc + timedelta(minutes=30)
    assert b.open == Decimal("180.0")
    assert b.close == Decimal("181.0")
    assert b.adj_factor == Decimal("1")
    assert b.source == "yfinance"


def test_yfinance_df_to_bars_forward_adjust_scales_prices() -> None:
    ad = _yf_adapter()
    iid = InstrumentId(market=Market.US, venue=Venue.NASDAQ,
                       asset_type=AssetType.EQUITY, symbol="AAPL")
    ts = datetime(2024, 1, 3, tzinfo=timezone.utc)
    # Adj Close half of Close → cumulative adjust factor 0.5.
    df = _FakeDF([
        (ts, {"Open": "100", "High": "110", "Low": "90",
              "Close": "100", "Adj Close": "50", "Volume": 0}),
    ])
    bars = list(ad._df_to_bars(df, iid, adjust="forward"))
    b = bars[0]
    assert b.adj_factor == Decimal("0.5")
    # All price points multiplied by adj_factor.
    assert b.open == Decimal("50")
    assert b.close == Decimal("50")
    assert b.high == Decimal("55")
    assert b.low == Decimal("45")


def test_yfinance_df_to_bars_missing_volume_defaults_zero() -> None:
    ad = _yf_adapter()
    iid = InstrumentId(market=Market.US, venue=Venue.NASDAQ,
                       asset_type=AssetType.EQUITY, symbol="AAPL")
    ts = datetime(2024, 1, 3, tzinfo=timezone.utc)
    df = _FakeDF([
        (ts, {"Open": "1", "High": "1", "Low": "1", "Close": "1",
              "Adj Close": "1"}),
    ])
    bars = list(ad._df_to_bars(df, iid, adjust="none"))
    assert bars[0].volume == Decimal("0")


def test_akshare_df_to_bars_chinese_columns() -> None:
    ad = _ak_adapter()
    iid = InstrumentId(market=Market.CN, venue=Venue.SSE,
                       asset_type=AssetType.EQUITY, symbol="600519")
    row = {
        "日期": "2024-01-03",
        "开盘": "1650.00",
        "收盘": "1660.00",
        "最高": "1670.00",
        "最低": "1640.00",
        "成交量": "1000000",
        "成交额": "1660000000",
    }
    df = _FakeDF([(0, row)], columns=list(row.keys()))
    bars = list(ad._df_to_bars(df, iid))
    assert len(bars) == 1
    b = bars[0]
    assert b.market_local_date == date(2024, 1, 3)
    # 07:00 UTC == 15:00 Asia/Shanghai session close.
    assert b.event_time_utc == datetime(2024, 1, 3, 7, 0, tzinfo=timezone.utc)
    assert b.available_at_utc == b.event_time_utc + timedelta(hours=1)
    assert b.open == Decimal("1650.00")
    assert b.close == Decimal("1660.00")
    assert b.volume == Decimal("1000000")
    assert b.turnover == Decimal("1660000000")
    assert b.adj_factor == Decimal("1")
    assert b.source == "akshare"


def test_akshare_venue_for_code_mapping() -> None:
    m = AkshareAdapter._venue_for_code
    assert m("600519") is Venue.SSE
    assert m("688981") is Venue.SSE
    assert m("000001") is Venue.SZSE
    assert m("300750") is Venue.SZSE
    assert m("430047") is Venue.BSE
    assert m("870204") is Venue.BSE
    # Unknown prefix falls back to SSE (curated aliases override in prod).
    assert m("999999") is Venue.SSE
