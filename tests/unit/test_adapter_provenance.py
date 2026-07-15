"""Provenance round-trip tests — spec §7.4 unified Adapter contract.

Every Bar emitted by an adapter must carry the three provenance fields
``source_version``, ``license_tag``, and ``quality_status``. Those fields
must survive a write into :class:`SqlBarRepository` and a subsequent read.

The AKShare and yfinance real adapters cannot be instantiated without the
optional pandas-based dependencies, so we assert their class-level
provenance attributes (which are stamped onto every Bar they yield via the
``_df_to_bars`` transform) directly. The fake adapter is exercised
end-to-end because it is a zero-dependency deterministic walk.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from packages.common.instrument_id import (
    AssetType, InstrumentId, Market, Venue,
)
from packages.data_sources.adapters.akshare_adapter import AkshareAdapter
from packages.data_sources.adapters.fake import FakeMarketDataAdapter
from packages.data_sources.adapters.yfinance_adapter import YFinanceAdapter
from packages.data_sources.contracts import Bar
from packages.data_sources.sql_bar_repo import (
    SqlBarRepository, metadata,
)


IID = InstrumentId(market=Market.CN, venue=Venue.SSE,
                   asset_type=AssetType.EQUITY, symbol="600519")


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    return eng


# -- class-level provenance on real adapters --------------------------
def test_akshare_adapter_declares_provenance():
    """AKShare adapter carries CN-market provenance identifiers."""
    assert AkshareAdapter.source_version == "akshare.v1"
    assert AkshareAdapter.license_tag == "PROVIDER_TOS"
    # adapter_id doubles as ``source`` on every emitted Bar.
    assert AkshareAdapter.adapter_id == "akshare"


def test_yfinance_adapter_declares_provenance():
    """yfinance adapter carries US-market provenance identifiers."""
    assert YFinanceAdapter.source_version == "yfinance.v1"
    assert YFinanceAdapter.license_tag == "PROVIDER_TOS"
    assert YFinanceAdapter.adapter_id == "yfinance"


# -- fake adapter emits stamped bars ----------------------------------
def test_fake_adapter_stamps_provenance():
    adapter = FakeMarketDataAdapter()
    bars = list(adapter.fetch_bars_daily(IID, date(2026, 1, 5), date(2026, 1, 9)))
    assert bars, "expected sessions in Mon–Fri window"
    for b in bars:
        assert b.source == "fake"
        assert b.source_version == "fake.v1"
        assert b.license_tag == "INTERNAL_RESEARCH"
        assert b.quality_status == "NORMAL"


# -- Bar dataclass defaults + SQL round-trip --------------------------
def test_bar_defaults_backwards_compatible():
    """Legacy call sites that omit provenance still construct valid Bars."""
    et = datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc)
    b = Bar(
        instrument_id=IID,
        event_time_utc=et,
        market_local_date=date(2026, 1, 6),
        open=Decimal("10"), high=Decimal("10"), low=Decimal("10"),
        close=Decimal("10"),
        volume=Decimal("1"), turnover=None, adj_factor=Decimal("1"),
        available_at_utc=et,
        source="legacy", calendar_version="v1", rule_version="v1",
    )
    assert b.source_version == "unspecified"
    assert b.license_tag == "INTERNAL_RESEARCH"
    assert b.quality_status == "NORMAL"


def test_sql_bar_repo_round_trips_provenance(engine):
    """Write bars w/ provenance → read back → the three fields survive."""
    repo = SqlBarRepository(engine)
    et = datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc)
    bar = Bar(
        instrument_id=IID,
        event_time_utc=et,
        market_local_date=date(2026, 1, 6),
        open=Decimal("10.00"), high=Decimal("10.20"), low=Decimal("9.80"),
        close=Decimal("10.10"),
        volume=Decimal("100"), turnover=Decimal("1010"),
        adj_factor=Decimal("1"),
        available_at_utc=et,
        source="akshare", calendar_version="cn.v0", rule_version="cn.v0",
        source_version="akshare.v1",
        license_tag="PROVIDER_TOS",
        quality_status="NORMAL",
    )
    assert repo.upsert_many([bar]) == 1

    got = repo.find_range(IID, date(2026, 1, 6), date(2026, 1, 6))
    assert len(got) == 1
    r = got[0]
    assert r.source_version == "akshare.v1"
    assert r.license_tag == "PROVIDER_TOS"
    assert r.quality_status == "NORMAL"


def test_sql_bar_repo_defaults_backfill_when_not_stamped(engine):
    """Bars constructed without provenance land with contract-default values."""
    repo = SqlBarRepository(engine)
    et = datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc)
    bar = Bar(
        instrument_id=IID,
        event_time_utc=et,
        market_local_date=date(2026, 1, 7),
        open=Decimal("10"), high=Decimal("10"), low=Decimal("10"),
        close=Decimal("10"),
        volume=Decimal("1"), turnover=None, adj_factor=Decimal("1"),
        available_at_utc=et,
        source="legacy", calendar_version="v1", rule_version="v1",
    )
    assert repo.upsert_many([bar]) == 1

    got = repo.find_range(IID, date(2026, 1, 7), date(2026, 1, 7))
    assert len(got) == 1
    assert got[0].source_version == "unspecified"
    assert got[0].license_tag == "INTERNAL_RESEARCH"
    assert got[0].quality_status == "NORMAL"


# -- provenance on other adapter contracts (spec §7.4 applies to all) --
def test_fund_nav_carries_provenance_defaults():
    from packages.data_sources.contracts import FundNAV
    et = datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc)
    nav = FundNAV(
        instrument_id=IID, market_local_date=date(2026, 1, 6),
        event_time_utc=et, unit_nav=Decimal("1.0"), accum_nav=None,
        available_at_utc=et, source="akshare",
    )
    assert nav.source_version == "unspecified"
    assert nav.license_tag == "INTERNAL_RESEARCH"
    assert nav.quality_status == "NORMAL"


def test_fund_nav_accepts_stamped_provenance():
    from packages.data_sources.contracts import FundNAV
    et = datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc)
    nav = FundNAV(
        instrument_id=IID, market_local_date=date(2026, 1, 6),
        event_time_utc=et, unit_nav=Decimal("1.0"), accum_nav=None,
        available_at_utc=et, source="akshare",
        source_version="akshare.v1", license_tag="PROVIDER_TOS",
        quality_status="NORMAL",
    )
    assert nav.source_version == "akshare.v1"
    assert nav.license_tag == "PROVIDER_TOS"


def test_corporate_action_carries_provenance_defaults():
    from packages.data_sources.contracts import CorporateAction
    ca = CorporateAction(
        instrument_id=IID, action_type="SPLIT",
        announcement_date_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ex_date_local=date(2026, 1, 6), payable_date_local=None,
        ratio=Decimal("2"), currency="CNY", source="akshare",
        available_at_utc=datetime(2026, 1, 6, tzinfo=timezone.utc),
    )
    assert ca.source_version == "unspecified"
    assert ca.license_tag == "INTERNAL_RESEARCH"
    assert ca.quality_status == "NORMAL"


# -- corporate_action + fund_nav SQL round-trips ----------------------
def _ca_engine():
    from packages.data_sources.sql_repos import metadata as aux_metadata
    eng = sa.create_engine("sqlite:///:memory:")
    aux_metadata.create_all(eng)
    return eng


def test_sql_corp_action_round_trips_provenance():
    from packages.data_sources.contracts import CorporateAction
    from packages.data_sources.sql_repos import SqlCorporateActionRepository

    eng = _ca_engine()
    repo = SqlCorporateActionRepository(eng)
    ca = CorporateAction(
        instrument_id=IID, action_type="SPLIT",
        announcement_date_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ex_date_local=date(2026, 1, 6), payable_date_local=None,
        ratio=Decimal("2"), currency="CNY", source="akshare",
        available_at_utc=datetime(2026, 1, 6, tzinfo=timezone.utc),
        source_version="akshare.v1", license_tag="PROVIDER_TOS",
        quality_status="NORMAL",
    )
    assert repo.upsert_many([ca]) == 1
    got = repo.list_for_instrument(IID)
    assert len(got) == 1
    assert got[0].source_version == "akshare.v1"
    assert got[0].license_tag == "PROVIDER_TOS"
    assert got[0].quality_status == "NORMAL"


def test_sql_corp_action_defaults_when_unstamped():
    from packages.data_sources.contracts import CorporateAction
    from packages.data_sources.sql_repos import SqlCorporateActionRepository

    eng = _ca_engine()
    repo = SqlCorporateActionRepository(eng)
    ca = CorporateAction(
        instrument_id=IID, action_type="DIVIDEND",
        announcement_date_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ex_date_local=date(2026, 2, 6), payable_date_local=None,
        ratio=Decimal("0.5"), currency="CNY", source="legacy",
        available_at_utc=datetime(2026, 2, 6, tzinfo=timezone.utc),
    )
    assert repo.upsert_many([ca]) == 1
    got = repo.list_for_instrument(IID)
    assert got[0].source_version == "unspecified"
    assert got[0].license_tag == "INTERNAL_RESEARCH"


def test_sql_fund_nav_round_trips_provenance():
    from packages.data_sources.contracts import FundNAV
    from packages.data_sources.sql_repos import SqlFundNavRepository

    fund_iid = InstrumentId(market=Market.CN, venue=Venue.CN_FUND,
                            asset_type=AssetType.FUND, symbol="000001")
    eng = _ca_engine()
    repo = SqlFundNavRepository(eng)
    et = datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc)
    nav = FundNAV(
        instrument_id=fund_iid, market_local_date=date(2026, 1, 6),
        event_time_utc=et, unit_nav=Decimal("1.234"),
        accum_nav=Decimal("1.500"), available_at_utc=et,
        source="akshare",
        source_version="akshare.v1", license_tag="PROVIDER_TOS",
        quality_status="NORMAL",
    )
    assert repo.upsert_many([nav]) == 1
    got = repo.find_range(fund_iid, date(2026, 1, 6), date(2026, 1, 6))
    assert len(got) == 1
    assert got[0].source_version == "akshare.v1"
    assert got[0].license_tag == "PROVIDER_TOS"
    assert got[0].quality_status == "NORMAL"
