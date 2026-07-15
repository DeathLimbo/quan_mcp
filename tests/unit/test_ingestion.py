from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from packages.common import (
    AssetType, DataConflictError, InstrumentId, Market, Venue, UnknownInstrumentError,
)
from packages.data_sources.adapters.fake import FakeMarketDataAdapter
from packages.data_sources.contracts import Bar
from packages.data_sources.registry import register_adapter, get_adapter, _reset_for_tests
from packages.data_quality.checks import BarChecks, Severity, has_errors
from packages.ingestion import (
    InMemoryWatermarkStore, Watermark, ingest_bars_daily,
)
from packages.instrument.service import InMemoryInstrumentRepo, InstrumentService


IID = InstrumentId(Market.US, Venue.NASDAQ, AssetType.EQUITY, "AAPL")


def test_registry_roundtrip():
    _reset_for_tests()
    a = FakeMarketDataAdapter()
    register_adapter("fake", a)
    assert get_adapter("fake") is a


def test_fake_adapter_produces_valid_bars():
    adapter = FakeMarketDataAdapter()
    bars = list(adapter.fetch_bars_daily(IID, date(2026, 1, 5), date(2026, 1, 9)))
    assert len(bars) == 5  # Mon..Fri, all sessions
    findings = BarChecks().run(bars)
    assert not has_errors(findings), [f for f in findings if f.severity is Severity.ERROR]
    for b in bars:
        assert b.calendar_version and b.rule_version
        assert b.available_at_utc >= b.event_time_utc


def test_ingest_is_idempotent_via_watermark():
    adapter = FakeMarketDataAdapter()
    wms = InMemoryWatermarkStore()
    sink: list[Bar] = []
    r1 = ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 9), watermarks=wms, sink=sink)
    assert r1.written == 5
    # Rerun same range: everything should be watermark-skipped
    sink2: list[Bar] = []
    r2 = ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 9), watermarks=wms, sink=sink2)
    assert r2.written == 0
    assert r2.skipped_by_watermark == 5
    assert r2.watermark_after == r1.watermark_after
    assert len(sink) == 5  # sink not double-appended
    assert len(sink2) == 0


def test_ingest_advances_watermark_on_new_range():
    adapter = FakeMarketDataAdapter()
    wms = InMemoryWatermarkStore()
    ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 7), watermarks=wms)
    r = ingest_bars_daily(adapter, IID, date(2026, 1, 5), date(2026, 1, 9), watermarks=wms)
    assert r.written == 2   # only 8, 9 are new
    assert r.watermark_after == date(2026, 1, 9)


def test_watermark_regression_refused():
    wms = InMemoryWatermarkStore()
    wms.advance(Watermark("bars.daily", IID.canonical(), "fake", date(2026, 1, 9)))
    with pytest.raises(ValueError):
        wms.advance(Watermark("bars.daily", IID.canonical(), "fake", date(2026, 1, 8)))


def test_dq_flags_broken_bar():
    good = list(FakeMarketDataAdapter().fetch_bars_daily(IID, date(2026, 1, 5), date(2026, 1, 5)))[0]
    from dataclasses import replace
    bad = replace(good, high=Decimal("1.0"), low=Decimal("10.0"))
    findings = BarChecks().run([bad])
    assert any(f.rule == "high_ge_low" and f.severity is Severity.ERROR for f in findings)


def test_instrument_service_register_and_resolve():
    repo = InMemoryInstrumentRepo()
    svc = InstrumentService(repo)
    svc.register(IID, currency="USD", name_en="Apple")
    row = svc.get_required(IID)
    assert row.currency == "USD"
    svc.add_alias("AAPL", "yfinance", IID)
    assert svc.resolve("AAPL", "yfinance") == IID
    with pytest.raises(UnknownInstrumentError):
        svc.resolve("MISSING", "yfinance")
