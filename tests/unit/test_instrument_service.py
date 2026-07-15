"""Unit tests for packages.instruments.service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.common.errors import UnknownInstrumentError
from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.contracts import InstrumentDescriptor
from packages.instruments.service import (
    InMemoryInstrumentRepository,
    InstrumentRecord,
    InstrumentService,
)


def _mk_descriptor(symbol: str = "600519",
                   venue: Venue = Venue.SSE,
                   asset: AssetType = AssetType.EQUITY,
                   market: Market = Market.CN) -> InstrumentDescriptor:
    iid = InstrumentId(market=market, venue=venue, asset_type=asset, symbol=symbol)
    return InstrumentDescriptor(instrument_id=iid, name_local="贵州茅台", name_en="Moutai",
                                currency="CNY", lot_size=100,
                                first_trade_date=None, last_trade_date=None, status="ACTIVE")


def test_register_and_get_roundtrip() -> None:
    svc = InstrumentService(InMemoryInstrumentRepository())
    desc = _mk_descriptor()
    rec = svc.register(desc, aliases=("600519.SH",),
                       calendar_version="cn.v0", rule_version="cn.v0")
    assert rec.instrument_id == desc.instrument_id
    assert rec.aliases == ("600519.SH",)
    assert rec.ingested_at_utc is not None
    fetched = svc.get(desc.instrument_id)
    assert fetched.descriptor is desc


def test_get_unknown_raises() -> None:
    svc = InstrumentService(InMemoryInstrumentRepository())
    iid = InstrumentId(market=Market.CN, venue=Venue.SSE,
                       asset_type=AssetType.EQUITY, symbol="000001")
    with pytest.raises(UnknownInstrumentError) as e:
        svc.get(iid)
    assert e.value.details["instrument_id"] == iid.canonical()


def test_resolve_canonical_string() -> None:
    repo = InMemoryInstrumentRepository()
    svc = InstrumentService(repo)
    desc = _mk_descriptor()
    svc.register(desc)
    rec = svc.resolve(desc.instrument_id.canonical())
    assert rec.instrument_id == desc.instrument_id


def test_resolve_alias_versioned() -> None:
    repo = InMemoryInstrumentRepository()
    svc = InstrumentService(repo)
    d1 = _mk_descriptor(symbol="600519")
    d2 = _mk_descriptor(symbol="000001", venue=Venue.SZSE)
    svc.register(d1)
    svc.register(d2)
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(2022, 1, 1, tzinfo=timezone.utc)
    repo.add_alias("MOUT", source="bloomberg", target=d1.instrument_id,
                   valid_from_utc=t0, valid_to_utc=t1)
    repo.add_alias("MOUT", source="bloomberg", target=d2.instrument_id,
                   valid_from_utc=t1, valid_to_utc=None)

    # Before t1 → d1
    r = svc.resolve("MOUT", source="bloomberg",
                    as_of=t0 + timedelta(days=30))
    assert r.instrument_id == d1.instrument_id
    # After t1 → d2
    r = svc.resolve("MOUT", source="bloomberg",
                    as_of=t1 + timedelta(days=30))
    assert r.instrument_id == d2.instrument_id


def test_resolve_unknown_alias_raises() -> None:
    svc = InstrumentService(InMemoryInstrumentRepository())
    with pytest.raises(UnknownInstrumentError):
        svc.resolve("NOPE", source="bloomberg")


def test_all_lists_registered() -> None:
    svc = InstrumentService(InMemoryInstrumentRepository())
    svc.register(_mk_descriptor(symbol="600519"))
    svc.register(_mk_descriptor(symbol="600036"))
    assert len(svc.all()) == 2
