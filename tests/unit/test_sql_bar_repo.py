"""Unit tests for :mod:`packages.data_sources.sql_bar_repo`.

Verifies (a) idempotent (delete-then-insert) upsert semantics, (b) PIT reads via
``as_of_utc`` filtering, and (c) CHECK-constraint enforcement of the invariants
declared in migration 0003 (``available_at_utc >= event_time_utc``, ``high >= low``).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from packages.common.instrument_id import parse_instrument_id
from packages.data_sources.contracts import Bar
from packages.data_sources.sql_bar_repo import (
    SqlBarRepository, market_bar_table, metadata,
)

IID = parse_instrument_id("CN.SSE.EQUITY.600519")


def _bar(d: date, close: str = "100", *, source: str = "akshare",
         available: datetime | None = None) -> Bar:
    et = datetime(d.year, d.month, d.day, 7, 0, tzinfo=timezone.utc)
    return Bar(
        instrument_id=IID,
        event_time_utc=et,
        market_local_date=d,
        open=Decimal(close), high=Decimal(close), low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1000"), turnover=Decimal("100000"),
        adj_factor=Decimal("1"),
        available_at_utc=available or et,
        source=source,
        calendar_version="v1", rule_version="v1",
    )


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite:///:memory:")
    # Enable SQLite CHECK enforcement (default) + FK (not used here).
    with eng.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = ON")
    metadata.create_all(eng)
    return eng


# --------------------------------------------------------------------
def test_upsert_and_find_range(engine):
    repo = SqlBarRepository(engine)
    written = repo.upsert_many([
        _bar(date(2024, 6, 3), "10.00"),
        _bar(date(2024, 6, 4), "10.20"),
        _bar(date(2024, 6, 5), "10.10"),
    ])
    assert written == 3
    rows = repo.find_range(IID, date(2024, 6, 3), date(2024, 6, 5))
    assert [b.market_local_date for b in rows] == [
        date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5),
    ]
    assert rows[1].close == Decimal("10.200000")


def test_upsert_is_idempotent(engine):
    repo = SqlBarRepository(engine)
    repo.upsert_many([_bar(date(2024, 6, 3), "10.00")])
    repo.upsert_many([_bar(date(2024, 6, 3), "11.00")])  # same PK, new price
    rows = repo.find_range(IID, date(2024, 6, 3), date(2024, 6, 3))
    assert len(rows) == 1
    assert rows[0].close == Decimal("11.000000")


def test_pit_filter_hides_future_availability(engine):
    """Bars whose available_at_utc > as_of_utc must be filtered out."""
    repo = SqlBarRepository(engine)
    late = datetime(2024, 6, 5, 12, tzinfo=timezone.utc)
    repo.upsert_many([
        _bar(date(2024, 6, 3), "10.00"),
        _bar(date(2024, 6, 4), "10.20", available=late),  # not yet available at T1
    ])
    t1 = datetime(2024, 6, 4, 0, tzinfo=timezone.utc)
    rows = repo.find_range(IID, date(2024, 6, 3), date(2024, 6, 5), as_of_utc=t1)
    assert [b.market_local_date for b in rows] == [date(2024, 6, 3)]


def test_latest_respects_pit(engine):
    repo = SqlBarRepository(engine)
    late = datetime(2024, 6, 5, 12, tzinfo=timezone.utc)
    repo.upsert_many([
        _bar(date(2024, 6, 3), "10.00"),
        _bar(date(2024, 6, 4), "10.20", available=late),
    ])
    latest_now = repo.latest(IID)
    assert latest_now is not None
    assert latest_now.market_local_date == date(2024, 6, 4)
    # As-of before the future-published bar was consumable → falls back to 6-3.
    as_of = datetime(2024, 6, 4, 0, tzinfo=timezone.utc)
    latest_pit = repo.latest(IID, as_of_utc=as_of)
    assert latest_pit is not None
    assert latest_pit.market_local_date == date(2024, 6, 3)


def test_check_constraint_rejects_invalid_range(engine):
    """SQLite honours CHECK constraints: high < low must raise IntegrityError."""
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.insert(market_bar_table).values(
                instrument_id=IID.canonical(),
                market_local_date=date(2024, 6, 3),
                event_time_utc=datetime(2024, 6, 3, 7, tzinfo=timezone.utc),
                open=Decimal("10"),
                high=Decimal("5"),   # violates high >= low
                low=Decimal("6"),
                close=Decimal("7"),
                volume=Decimal("0"),
                available_at_utc=datetime(2024, 6, 3, 7, tzinfo=timezone.utc),
                source="test",
                calendar_version="v0",
                rule_version="v0",
                ingested_at_utc=datetime(2024, 6, 3, 7, tzinfo=timezone.utc),
            ))


def test_check_constraint_rejects_future_event(engine):
    """available_at_utc < event_time_utc violates PIT invariant."""
    et = datetime(2024, 6, 3, 20, tzinfo=timezone.utc)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.insert(market_bar_table).values(
                instrument_id=IID.canonical(),
                market_local_date=date(2024, 6, 3),
                event_time_utc=et,
                open=Decimal("10"), high=Decimal("10"),
                low=Decimal("10"), close=Decimal("10"),
                volume=Decimal("0"),
                available_at_utc=et.replace(hour=1),  # earlier than event
                source="test",
                calendar_version="v0", rule_version="v0",
                ingested_at_utc=et,
            ))


def test_find_range_empty(engine):
    repo = SqlBarRepository(engine)
    assert repo.find_range(IID, date(2024, 1, 1), date(2024, 1, 5)) == []
    assert repo.latest(IID) is None


def test_source_filter(engine):
    repo = SqlBarRepository(engine)
    repo.upsert_many([
        _bar(date(2024, 6, 3), "10.00", source="akshare"),
        _bar(date(2024, 6, 3), "10.05", source="tushare"),
    ])
    rows = repo.find_range(IID, date(2024, 6, 3), date(2024, 6, 3),
                           source="tushare")
    assert len(rows) == 1
    assert rows[0].source == "tushare"
    assert rows[0].close == Decimal("10.050000")
