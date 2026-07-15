"""End-to-end ingestion tests wiring the fake adapter through the ETL
pipeline into :class:`SqlBarRepository` (SqlBarSink adapter). Also covers
the strict DQ fail-closed path added in this iteration.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterator

import pytest
import sqlalchemy as sa

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue,
)
from packages.data_sources.adapters.fake import FakeMarketDataAdapter
from packages.data_sources.contracts import Bar, MarketDataAdapter
from packages.data_sources.sql_bar_repo import SqlBarRepository, metadata
from packages.ingestion import InMemoryWatermarkStore, ingest_bars_daily
from packages.ingestion.pipeline import ListSink, SqlBarSink


IID = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    return eng


def test_ingest_writes_to_sql_repo(engine):
    adapter = FakeMarketDataAdapter()
    wms = InMemoryWatermarkStore()
    repo = SqlBarRepository(engine)
    sink = SqlBarSink(repo)
    report = ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 9),
                                watermarks=wms, sink=sink)
    assert report.written == 5
    assert not report.dq_blocked
    got = repo.find_range(IID, date(2026, 1, 5), date(2026, 1, 9))
    assert len(got) == 5
    assert got[0].market_local_date == date(2026, 1, 5)
    # Rerun: nothing new should hit the sink (watermark blocks).
    report2 = ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 9),
                                 watermarks=wms, sink=sink)
    assert report2.written == 0
    assert report2.skipped_by_watermark == 5
    # Table should still hold exactly 5 rows.
    assert len(repo.find_range(IID, date(2026, 1, 5), date(2026, 1, 9))) == 5


def test_list_sink_still_supported(engine):
    """Backwards-compat: passing a plain list appends bars in order."""
    adapter = FakeMarketDataAdapter()
    wms = InMemoryWatermarkStore()
    sink: list[Bar] = []
    report = ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 9),
                                watermarks=wms, sink=sink)
    assert report.written == 5
    assert [b.market_local_date for b in sink] == [
        date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7),
        date(2026, 1, 8), date(2026, 1, 9),
    ]


class _BrokenAdapter(MarketDataAdapter):
    """Adapter that yields a bar with high<low so DQ layer 3 fails."""
    adapter_id = "broken"
    supports_markets = frozenset({Market.US})
    supports_asset_types = frozenset({AssetType.EQUITY})

    def list_instruments(self, market):
        return []

    def fetch_bars_daily(self, instrument_id, start, end, *, adjust="none") -> Iterator[Bar]:
        et = datetime(start.year, start.month, start.day, 21, tzinfo=timezone.utc)
        yield Bar(
            instrument_id=instrument_id,
            event_time_utc=et, market_local_date=start,
            open=Decimal("100"), high=Decimal("50"),  # violates high >= low
            low=Decimal("60"), close=Decimal("55"),
            volume=Decimal("1000"), turnover=None,
            adj_factor=Decimal("1"),
            available_at_utc=et,
            source="broken", calendar_version="v1", rule_version="v1",
        )


def test_strict_mode_blocks_write_on_dq_error(engine):
    wms = InMemoryWatermarkStore()
    repo = SqlBarRepository(engine)
    sink = SqlBarSink(repo)
    report = ingest_bars_daily(
        _BrokenAdapter(), IID,
        date(2026, 1, 5), date(2026, 1, 5),
        watermarks=wms, sink=sink,
    )
    assert report.dq_blocked is True
    assert report.written == 0
    assert report.watermark_after is None  # untouched
    # Nothing was persisted.
    assert repo.find_range(IID, date(2026, 1, 5), date(2026, 1, 5)) == []
    # Findings surface the offending rule.
    assert any(f.rule == "high_ge_low" for f in report.findings)


def test_non_strict_mode_writes_even_with_error(engine):
    wms = InMemoryWatermarkStore()
    sink = ListSink()
    report = ingest_bars_daily(
        _BrokenAdapter(), IID,
        date(2026, 1, 5), date(2026, 1, 5),
        watermarks=wms, sink=sink,
        strict=False,
    )
    assert report.dq_blocked is False
    assert report.written == 1
    assert len(sink.bars) == 1
    assert any(f.rule == "high_ge_low" for f in report.findings)


def test_list_sink_helper_class():
    """ListSink implements the BarSink protocol and preserves order."""
    adapter = FakeMarketDataAdapter()
    wms = InMemoryWatermarkStore()
    sink = ListSink()
    report = ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 7),
                                watermarks=wms, sink=sink)
    assert report.written == 3
    assert [b.market_local_date for b in sink.bars] == [
        date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7),
    ]
