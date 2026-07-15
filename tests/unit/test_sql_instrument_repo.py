"""SQLite-backed integration tests for SqlInstrumentRepository."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.contracts import InstrumentDescriptor
from packages.instruments.service import InstrumentRecord, InstrumentService
from packages.instruments.sql_repo import SqlInstrumentRepository, metadata


@pytest.fixture()
def engine():
    e = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    metadata.create_all(e)
    yield e
    e.dispose()


def _desc(symbol: str = "600519", venue: Venue = Venue.SSE,
          asset: AssetType = AssetType.EQUITY,
          market: Market = Market.CN) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        instrument_id=InstrumentId(market=market, venue=venue,
                                   asset_type=asset, symbol=symbol),
        name_local="贵州茅台", name_en="Moutai",
        currency="CNY", lot_size=100,
        first_trade_date=None, last_trade_date=None, status="ACTIVE",
    )


def test_upsert_and_get(engine):
    repo = SqlInstrumentRepository(engine)
    d = _desc()
    rec = repo.upsert(InstrumentRecord(descriptor=d))
    assert rec.ingested_at_utc is not None
    fetched = repo.get(d.instrument_id)
    assert fetched is not None
    assert fetched.descriptor.instrument_id == d.instrument_id
    assert fetched.descriptor.currency == "CNY"


def test_upsert_is_idempotent(engine):
    repo = SqlInstrumentRepository(engine)
    d = _desc()
    repo.upsert(InstrumentRecord(descriptor=d))
    # Second upsert with modified name overwrites, PK unchanged.
    d2 = InstrumentDescriptor(
        instrument_id=d.instrument_id, name_local="NEW", name_en=None,
        currency="CNY", lot_size=200, first_trade_date=None,
        last_trade_date=None, status="SUSPENDED",
    )
    repo.upsert(InstrumentRecord(descriptor=d2))
    got = repo.get(d.instrument_id)
    assert got.descriptor.name_local == "NEW"
    assert got.descriptor.lot_size == 200
    assert got.descriptor.status == "SUSPENDED"
    assert len(repo.all()) == 1


def test_resolve_alias_versioned(engine):
    repo = SqlInstrumentRepository(engine)
    d1 = _desc(symbol="600519")
    d2 = _desc(symbol="000001", venue=Venue.SZSE)
    repo.upsert(InstrumentRecord(descriptor=d1))
    repo.upsert(InstrumentRecord(descriptor=d2))
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(2022, 1, 1, tzinfo=timezone.utc)
    repo.add_alias("MOUT", source="bbg", target=d1.instrument_id,
                   valid_from_utc=t0, valid_to_utc=t1)
    repo.add_alias("MOUT", source="bbg", target=d2.instrument_id,
                   valid_from_utc=t1, valid_to_utc=None)

    r = repo.resolve_alias("MOUT", source="bbg",
                           as_of=t0 + timedelta(days=30))
    assert r is not None and r.descriptor.instrument_id == d1.instrument_id
    r = repo.resolve_alias("MOUT", source="bbg",
                           as_of=t1 + timedelta(days=30))
    assert r is not None and r.descriptor.instrument_id == d2.instrument_id


def test_resolve_alias_missing_returns_none(engine):
    repo = SqlInstrumentRepository(engine)
    assert repo.resolve_alias("NOPE", source="bbg") is None


def test_service_uses_sql_repo_end_to_end(engine):
    repo = SqlInstrumentRepository(engine)
    svc = InstrumentService(repo)
    d = _desc()
    svc.register(d)
    rec = svc.resolve(d.instrument_id.canonical())
    assert rec.descriptor.instrument_id == d.instrument_id
    assert len(svc.all()) == 1


def test_get_unknown_returns_none(engine):
    repo = SqlInstrumentRepository(engine)
    iid = InstrumentId(market=Market.CN, venue=Venue.SSE,
                       asset_type=AssetType.EQUITY, symbol="000001")
    assert repo.get(iid) is None
