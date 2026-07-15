"""Env-gated live-adapter smoke tests (spec §7 real network integration).

Default behaviour: skip. These tests only run when ``RUN_LIVE_ADAPTERS=1``
is set in the environment AND the underlying provider package is importable
AND the network fetch actually returns non-empty data. Any of those failing
skips the test rather than reporting a false negative — real-world network
flakiness must not turn CI red on a green codebase.

What we prove when the test runs:
1. The adapter can round-trip a real one-day slice against the real provider.
2. Each emitted ``Bar`` carries the provenance trio stamped by §7.4.
3. ``available_at_utc >= event_time_utc`` — PIT contract holds on live data.
4. ``SqlBarRepository`` persists the live bars and returns them intact,
   including the provenance columns.

These tests exist so a CI environment with data-source extras installed can
prove the transform layer works against the real APIs, not just handcrafted
fixture DataFrames.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue,
)
from packages.data_sources.sql_bar_repo import SqlBarRepository, metadata


LIVE_ENABLED = os.environ.get("RUN_LIVE_ADAPTERS") == "1"
_SKIP_REASON = "set RUN_LIVE_ADAPTERS=1 to run live adapter smoke tests"


def _recent_business_range() -> tuple[date, date]:
    """A ~10-day window ending 3 business days ago so weekends/holidays are
    unlikely to yield an empty result. Not exact — we just want SOME bars."""
    today = date.today()
    end = today - timedelta(days=3)
    start = end - timedelta(days=14)
    return start, end


def _assert_provenance(bar) -> None:
    assert bar.source_version and bar.source_version != "unspecified"
    assert bar.license_tag and bar.license_tag != "INTERNAL_RESEARCH"
    assert bar.quality_status == "NORMAL"
    assert bar.available_at_utc >= bar.event_time_utc


def _in_memory_engine():
    eng = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    return eng


# ---------------------------------------------------------------------
# AKShare — CN market.
# ---------------------------------------------------------------------
@pytest.mark.skipif(not LIVE_ENABLED, reason=_SKIP_REASON)
def test_akshare_live_daily_bars_roundtrip():
    ak = pytest.importorskip("akshare",
                              reason="akshare not installed in this env")
    from packages.data_sources.adapters.akshare_adapter import AkshareAdapter

    adapter = AkshareAdapter()
    # 600519 = Kweichow Moutai, SSE main-board — one of the most reliable
    # constituents to have historical data.
    iid = InstrumentId(market=Market.CN, venue=Venue.SSE,
                        asset_type=AssetType.EQUITY, symbol="600519")
    start, end = _recent_business_range()
    try:
        bars = list(adapter.fetch_bars_daily(iid, start, end))
    except Exception as e:
        pytest.skip(f"live akshare fetch failed (network?): {e}")
    if not bars:
        pytest.skip("live akshare fetch returned zero rows (holiday window?)")

    for b in bars:
        _assert_provenance(b)
        assert b.source == "akshare"
        assert b.source_version == "akshare.v1"
        assert b.license_tag == "PROVIDER_TOS"
        assert b.instrument_id == iid

    engine = _in_memory_engine()
    repo = SqlBarRepository(engine)
    written = repo.upsert_many(bars)
    assert written == len(bars)

    # Round-trip: pull back and confirm provenance survives persistence.
    got = repo.find_range(iid, bars[0].market_local_date,
                           bars[-1].market_local_date)
    assert len(got) == len(bars)
    for gb in got:
        _assert_provenance(gb)
        assert gb.source_version == "akshare.v1"


# ---------------------------------------------------------------------
# yfinance — US market.
# ---------------------------------------------------------------------
@pytest.mark.skipif(not LIVE_ENABLED, reason=_SKIP_REASON)
def test_yfinance_live_daily_bars_roundtrip():
    yf = pytest.importorskip("yfinance",
                              reason="yfinance not installed in this env")
    from packages.data_sources.adapters.yfinance_adapter import YFinanceAdapter

    adapter = YFinanceAdapter()
    # AAPL — one of the most reliable US listings.
    iid = InstrumentId(market=Market.US, venue=Venue.NASDAQ,
                        asset_type=AssetType.EQUITY, symbol="AAPL")
    start, end = _recent_business_range()
    try:
        bars = list(adapter.fetch_bars_daily(iid, start, end))
    except Exception as e:
        pytest.skip(f"live yfinance fetch failed (network?): {e}")
    if not bars:
        pytest.skip("live yfinance fetch returned zero rows (holiday window?)")

    for b in bars:
        _assert_provenance(b)
        assert b.source == "yfinance"
        assert b.source_version == "yfinance.v1"
        assert b.license_tag == "PROVIDER_TOS"
        assert b.instrument_id == iid

    engine = _in_memory_engine()
    repo = SqlBarRepository(engine)
    written = repo.upsert_many(bars)
    assert written == len(bars)

    got = repo.find_range(iid, bars[0].market_local_date,
                           bars[-1].market_local_date)
    assert len(got) == len(bars)
    for gb in got:
        _assert_provenance(gb)
        assert gb.source_version == "yfinance.v1"


# ---------------------------------------------------------------------
# Meta test — always runs so we catch regressions in the skip machinery
# itself. Confirms the gate is wired: without the env var set, the two
# live tests above must be marked skipped rather than collected as passes.
# ---------------------------------------------------------------------
def test_live_adapter_tests_are_env_gated():
    if LIVE_ENABLED:
        pytest.skip("live mode enabled; skip-machinery meta-check not applicable")
    # If we got here, the env is *not* set. The two decorated tests must
    # therefore be skipped -- we verify by checking their skip markers exist
    # on the module.
    from tests.unit import test_live_adapters as mod
    for fn_name in ("test_akshare_live_daily_bars_roundtrip",
                    "test_yfinance_live_daily_bars_roundtrip"):
        fn = getattr(mod, fn_name)
        marks = [m for m in getattr(fn, "pytestmark", [])]
        assert any(m.name == "skipif" for m in marks), (
            f"{fn_name} lost its skipif marker"
        )
