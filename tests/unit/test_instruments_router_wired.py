"""End-to-end tests for /v1/instruments backed by a real InstrumentService."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_instrument_service
from apps.api.main import app
from packages.common.instrument_id import AssetType, InstrumentId, Market, Venue
from packages.data_sources.contracts import InstrumentDescriptor
from packages.instruments.service import (
    InMemoryInstrumentRepository, InstrumentService,
)


@pytest.fixture()
def client_with_service():
    repo = InMemoryInstrumentRepository()
    svc = InstrumentService(repo)
    iid = InstrumentId(market=Market.CN, venue=Venue.SSE,
                       asset_type=AssetType.EQUITY, symbol="600519")
    svc.register(InstrumentDescriptor(
        instrument_id=iid, name_local="贵州茅台", name_en="Moutai",
        currency="CNY", lot_size=100, first_trade_date=None,
        last_trade_date=None, status="ACTIVE"))
    app.dependency_overrides[get_instrument_service] = lambda: svc
    try:
        yield TestClient(app), svc, iid
    finally:
        app.dependency_overrides.pop(get_instrument_service, None)


def test_resolve_returns_registered_descriptor(client_with_service):
    client, _svc, iid = client_with_service
    r = client.get("/v1/instruments/resolve", params={"q": iid.canonical()})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["instrument_id"] == iid.canonical()
    # Enriched from descriptor:
    assert body["data"]["name_local"] == "贵州茅台"
    assert body["data"]["currency"] == "CNY"
    assert "known" not in body["data"]     # full record returned


def test_resolve_unknown_canonical_still_returns_parsed(client_with_service):
    client, _svc, _iid = client_with_service
    r = client.get("/v1/instruments/resolve",
                   params={"q": "CN.SZSE.EQUITY.000001"})
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["known"] is False
    assert body["data"]["market"] == "CN"


def test_resolve_alias_lookup(client_with_service):
    from datetime import datetime, timezone
    client, svc, iid = client_with_service
    # Alias lives on the repo, not the service.
    svc._repo.add_alias(  # type: ignore[attr-defined]
        "MOUT", source="bbg", target=iid,
        valid_from_utc=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
    r = client.get("/v1/instruments/resolve",
                   params={"q": "MOUT", "source": "bbg"})
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["instrument_id"] == iid.canonical()


def test_resolve_alias_unknown_returns_error_envelope(client_with_service):
    client, _svc, _iid = client_with_service
    r = client.get("/v1/instruments/resolve",
                   params={"q": "NOPE", "source": "bbg"})
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "UNKNOWN_INSTRUMENT"


def test_get_by_id_registered(client_with_service):
    client, _svc, iid = client_with_service
    r = client.get(f"/v1/instruments/{iid.canonical()}")
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["instrument_id"] == iid.canonical()
    assert body["data"]["ingested_at_utc"] is not None


def test_get_by_id_unregistered(client_with_service):
    client, _svc, _iid = client_with_service
    r = client.get("/v1/instruments/CN.SZSE.EQUITY.000001")
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["known"] is False
