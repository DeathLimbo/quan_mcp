"""Smoke tests for the Phase-1 data-status / fundamentals / admin-ingestion routers.

The three routers all read collaborators from ``app.state``; the tests
inject stubs so no DB is required. Each test asserts:

- the envelope shape (``ok`` == True on success, False on validation errors),
- the specific payload fields that downstream MCP tools rely on,
- point-in-time semantics (facts filtered by ``as_of``),
- fail-closed behavior when a collaborator is missing.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.common.instrument_id import parse_instrument_id
from packages.data_sources.contracts import Bar
from packages.data_sources.registry import AdapterRegistry
from packages.fundamentals.facts import Fact, FactName, FactStore, PitQuery
from packages.ingestion.watermark import InMemoryWatermarkStore

CLIENT = TestClient(app)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _bar(iid_str: str, d: date, close: Decimal, available: datetime) -> Bar:
    iid = parse_instrument_id(iid_str)
    return Bar(
        instrument_id=iid,
        event_time_utc=datetime(d.year, d.month, d.day, 20, tzinfo=timezone.utc),
        market_local_date=d,
        open=close, high=close, low=close, close=close,
        volume=Decimal("1000"), turnover=None,
        adj_factor=Decimal("1"),
        available_at_utc=available,
        source="stub", calendar_version="v0", rule_version="v0",
    )


class _InMemBarRepo:
    """Minimum-viable stand-in for :class:`SqlBarRepository`."""

    def __init__(self, bars: Iterable[Bar]) -> None:
        self._bars = list(bars)

    def latest(self, iid, *, source: str | None = None, as_of_utc: datetime | None = None):
        rows = [b for b in self._bars if b.instrument_id.canonical() == iid.canonical()]
        if source:
            rows = [b for b in rows if b.source == source]
        if as_of_utc is not None:
            rows = [b for b in rows if b.available_at_utc <= as_of_utc]
        if not rows:
            return None
        return max(rows, key=lambda b: b.market_local_date)

    def upsert_many(self, bars):
        n = 0
        for b in bars:
            self._bars.append(b); n += 1
        return n


class _FactRepoAdapter:
    """Adapts an in-memory ``FactStore`` to the router's ``get_as_of`` shape."""

    def __init__(self, store: FactStore) -> None:
        self._store = store

    def get_as_of(self, iid, name: FactName, as_of):
        return self._store.get(PitQuery(instrument_id=iid, name=name, as_of=as_of))


class _StubAdapter:
    """Tiny adapter that yields a fixed bar sequence."""

    adapter_id = "stub"

    def __init__(self, bars):
        self._bars = bars

    def fetch_bars_daily(self, iid, start, end):
        return iter(self._bars)


# ---------------------------------------------------------------------
# GET /v1/data/status
# ---------------------------------------------------------------------
def test_data_status_returns_watermark_when_bars_exist():
    iid = "US.NASDAQ.EQUITY.AAPL"
    now = datetime(2024, 6, 6, tzinfo=timezone.utc)
    bar = _bar(iid, date(2024, 6, 5), Decimal("100"),
               available=now - __import__("datetime").timedelta(hours=1))
    app.state.bar_repo = _InMemBarRepo([bar])
    r = CLIENT.get(f"/v1/data/status?instrument_id={iid}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["quality_status"] in ("OK", "STALE")
    assert body["data"]["latest_market_local_date"] == "2024-06-05"
    del app.state.bar_repo


def test_data_status_empty_when_no_bars():
    app.state.bar_repo = _InMemBarRepo([])
    r = CLIENT.get("/v1/data/status?instrument_id=US.NASDAQ.EQUITY.AAPL")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["quality_status"] == "EMPTY"
    del app.state.bar_repo


def test_data_status_missing_repo_fails_closed():
    # No app.state.bar_repo — router must NOT fabricate data.
    if hasattr(app.state, "bar_repo"):
        del app.state.bar_repo
    r = CLIENT.get("/v1/data/status?instrument_id=US.NASDAQ.EQUITY.AAPL")
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "DATA_NOT_READY"


def test_data_status_rejects_bad_instrument_id():
    app.state.bar_repo = _InMemBarRepo([])
    r = CLIENT.get("/v1/data/status?instrument_id=not_canonical")
    body = r.json()
    assert body["ok"] is False
    del app.state.bar_repo


# ---------------------------------------------------------------------
# GET /v1/fundamentals/facts
# ---------------------------------------------------------------------
def _mk_fact(iid_str, name, value, as_of, avail):
    iid = parse_instrument_id(iid_str)
    return Fact(
        instrument_id=iid, name=name,
        period_end=as_of.date(),
        value=Decimal(str(value)), currency="USD",
        as_of_utc=as_of, available_at_utc=avail,
        source="stub", source_version="1",
    )


def test_fundamentals_returns_pit_latest():
    store = FactStore()
    iid = "US.NASDAQ.EQUITY.AAPL"
    # Two revenue prints; older visible earlier, newer only after 2024-05-01.
    older = _mk_fact(iid, FactName.REVENUE, 100,
                     datetime(2024, 3, 31, tzinfo=timezone.utc),
                     datetime(2024, 4, 30, tzinfo=timezone.utc))
    newer = _mk_fact(iid, FactName.REVENUE, 120,
                     datetime(2024, 6, 30, tzinfo=timezone.utc),
                     datetime(2024, 7, 15, tzinfo=timezone.utc))
    store.add_many([older, newer])
    app.state.fact_repo = _FactRepoAdapter(store)

    # As of 2024-05-15 → only older is visible.
    r = CLIENT.get(
        f"/v1/fundamentals/facts?instrument_id={iid}"
        f"&fact_names=revenue&as_of=2024-05-15T00:00:00Z"
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["facts"][0]["value"] == "100"
    assert body["data"]["missing"] == []

    # As of 2024-08-01 → newer wins.
    r = CLIENT.get(
        f"/v1/fundamentals/facts?instrument_id={iid}"
        f"&fact_names=revenue&as_of=2024-08-01T00:00:00Z"
    )
    body = r.json()
    assert body["data"]["facts"][0]["value"] == "120"
    del app.state.fact_repo


def test_fundamentals_missing_fact_names_in_missing_list():
    store = FactStore()
    app.state.fact_repo = _FactRepoAdapter(store)
    r = CLIENT.get(
        "/v1/fundamentals/facts?instrument_id=US.NASDAQ.EQUITY.AAPL"
        "&fact_names=revenue,eps&as_of=2024-08-01T00:00:00Z"
    )
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["facts"] == []
    assert set(body["data"]["missing"]) == {"revenue", "eps"}
    del app.state.fact_repo


def test_fundamentals_rejects_unknown_fact_name():
    app.state.fact_repo = _FactRepoAdapter(FactStore())
    r = CLIENT.get(
        "/v1/fundamentals/facts?instrument_id=US.NASDAQ.EQUITY.AAPL"
        "&fact_names=made_up_fact"
    )
    body = r.json()
    assert body["ok"] is False
    del app.state.fact_repo


# ---------------------------------------------------------------------
# POST /v1/admin/ingestion/jobs
# ---------------------------------------------------------------------
def test_admin_ingestion_writes_and_returns_report():
    iid = "US.NASDAQ.EQUITY.AAPL"
    bar = _bar(iid, date(2024, 6, 5), Decimal("100"),
               available=datetime(2024, 6, 5, 21, tzinfo=timezone.utc))
    adapter = _StubAdapter([bar])
    app.state.adapter_registry = AdapterRegistry({"stub": adapter})
    app.state.watermarks = InMemoryWatermarkStore()
    app.state.bar_repo = _InMemBarRepo([])

    r = CLIENT.post("/v1/admin/ingestion/jobs", json={
        "source": "stub",
        "instrument_id": iid,
        "start": "2024-06-01",
        "end": "2024-06-10",
        "strict": True,
    })
    body = r.json()
    assert body["ok"] is True, body
    assert body["data"]["written"] == 1
    assert body["data"]["dq_blocked"] is False
    assert body["data"]["watermark_after"] == "2024-06-05"
    # Repo received the bar.
    assert len(app.state.bar_repo._bars) == 1

    # Cleanup.
    for attr in ("adapter_registry", "watermarks", "bar_repo"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_admin_ingestion_unknown_adapter_fails_closed():
    app.state.adapter_registry = AdapterRegistry({})
    app.state.watermarks = InMemoryWatermarkStore()
    r = CLIENT.post("/v1/admin/ingestion/jobs", json={
        "source": "does_not_exist",
        "instrument_id": "US.NASDAQ.EQUITY.AAPL",
        "start": "2024-06-01",
        "end": "2024-06-10",
    })
    body = r.json()
    assert body["ok"] is False
    for attr in ("adapter_registry", "watermarks"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_admin_ingestion_dq_blocks_write_and_watermark():
    iid = "US.NASDAQ.EQUITY.AAPL"
    # Broken bar: high < low will trip Layer 2 DQ.
    bad = Bar(
        instrument_id=parse_instrument_id(iid),
        event_time_utc=datetime(2024, 6, 5, 20, tzinfo=timezone.utc),
        market_local_date=date(2024, 6, 5),
        open=Decimal("100"), high=Decimal("50"),
        low=Decimal("60"), close=Decimal("55"),
        volume=Decimal("1000"), turnover=None, adj_factor=Decimal("1"),
        available_at_utc=datetime(2024, 6, 5, 21, tzinfo=timezone.utc),
        source="stub", calendar_version="v0", rule_version="v0",
    )
    app.state.adapter_registry = AdapterRegistry({"stub": _StubAdapter([bad])})
    app.state.watermarks = InMemoryWatermarkStore()
    app.state.bar_repo = _InMemBarRepo([])
    r = CLIENT.post("/v1/admin/ingestion/jobs", json={
        "source": "stub",
        "instrument_id": iid,
        "start": "2024-06-01",
        "end": "2024-06-10",
        "strict": True,
    })
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["dq_blocked"] is True
    assert body["data"]["written"] == 0
    # Repo untouched → confirms fail-closed at the sink.
    assert len(app.state.bar_repo._bars) == 0
    for attr in ("adapter_registry", "watermarks", "bar_repo"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_admin_ingestion_missing_registry_fails_closed():
    for attr in ("adapter_registry", "watermarks", "bar_repo"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)
    r = CLIENT.post("/v1/admin/ingestion/jobs", json={
        "source": "stub",
        "instrument_id": "US.NASDAQ.EQUITY.AAPL",
        "start": "2024-06-01",
        "end": "2024-06-10",
    })
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "DATA_NOT_READY"
