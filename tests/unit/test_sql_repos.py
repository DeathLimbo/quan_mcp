"""Unit tests for :mod:`packages.data_sources.sql_repos` (corporate actions,
fundamentals, fund NAV, FX, portfolio positions).

Each repository verifies (1) idempotent upsert (delete-then-insert per PK),
(2) PIT-aware read via ``available_at_utc``, and (3) round-trip decoding.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from packages.common.instrument_id import parse_instrument_id
from packages.data_sources.contracts import CorporateAction, FundNAV
from packages.data_sources.sql_repos import (
    SqlCorporateActionRepository, SqlFundNavRepository,
    SqlFundamentalFactRepository, SqlFxRepository,
    SqlPortfolioPositionRepository, metadata,
)
from packages.fundamentals.facts import Fact, FactName

IID_A = parse_instrument_id("CN.SSE.EQUITY.600519")
IID_B = parse_instrument_id("US.NASDAQ.EQUITY.AAPL")
IID_F = parse_instrument_id("CN.CN_FUND.FUND.510300")


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    return eng


# ==================================================================
# Corporate actions
# ==================================================================
def test_ca_upsert_and_list(engine):
    repo = SqlCorporateActionRepository(engine)
    a = CorporateAction(
        instrument_id=IID_A, action_type="SPLIT",
        announcement_date_utc=datetime(2024, 5, 20, tzinfo=timezone.utc),
        ex_date_local=date(2024, 5, 28),
        payable_date_local=None,
        ratio=Decimal("2"), currency="CNY", source="akshare",
        available_at_utc=datetime(2024, 5, 21, tzinfo=timezone.utc),
    )
    assert repo.upsert_many([a]) == 1
    out = repo.list_for_instrument(IID_A)
    assert len(out) == 1
    assert out[0].action_type == "SPLIT"
    assert out[0].ratio == Decimal("2.0000000000")


def test_ca_upsert_idempotent(engine):
    repo = SqlCorporateActionRepository(engine)
    a = CorporateAction(
        instrument_id=IID_A, action_type="DIVIDEND",
        announcement_date_utc=datetime(2024, 6, 1, tzinfo=timezone.utc),
        ex_date_local=date(2024, 6, 11),
        payable_date_local=date(2024, 6, 20),
        ratio=Decimal("1.50"), currency="CNY", source="akshare",
        available_at_utc=datetime(2024, 6, 2, tzinfo=timezone.utc),
    )
    repo.upsert_many([a])
    a2 = dataclasses.replace(a, ratio=Decimal("1.75"))
    repo.upsert_many([a2])
    out = repo.list_for_instrument(IID_A)
    assert len(out) == 1
    assert out[0].ratio == Decimal("1.7500000000")


def test_ca_pit_filter(engine):
    repo = SqlCorporateActionRepository(engine)
    late = datetime(2024, 5, 30, tzinfo=timezone.utc)
    a = CorporateAction(
        instrument_id=IID_A, action_type="SPLIT",
        announcement_date_utc=datetime(2024, 5, 20, tzinfo=timezone.utc),
        ex_date_local=date(2024, 5, 28), payable_date_local=None,
        ratio=Decimal("2"), currency="CNY", source="akshare",
        available_at_utc=late,
    )
    repo.upsert_many([a])
    early = datetime(2024, 5, 25, tzinfo=timezone.utc)
    assert repo.list_for_instrument(IID_A, as_of_utc=early) == []
    assert len(repo.list_for_instrument(IID_A, as_of_utc=late)) == 1


# ==================================================================
# Fundamentals
# ==================================================================
def _fact(name: FactName, value: str, *, as_of: datetime,
          available: datetime | None = None,
          source: str = "sec") -> Fact:
    return Fact(
        instrument_id=IID_B, name=name,
        period_end=as_of.date(), value=Decimal(value),
        currency="USD", as_of_utc=as_of,
        available_at_utc=available or as_of,
        source=source, source_version="v1",
    )


def test_fact_upsert_and_get_as_of(engine):
    repo = SqlFundamentalFactRepository(engine)
    as_of_1 = datetime(2024, 4, 30, tzinfo=timezone.utc)
    as_of_2 = datetime(2024, 7, 31, tzinfo=timezone.utc)
    repo.upsert_many([
        _fact(FactName.EPS, "1.20", as_of=as_of_1),
        _fact(FactName.EPS, "1.35", as_of=as_of_2),
    ])
    early = datetime(2024, 6, 1, tzinfo=timezone.utc)
    got = repo.get_as_of(IID_B, FactName.EPS, early)
    assert got is not None and got.value == Decimal("1.2000000000")
    later = datetime(2024, 8, 15, tzinfo=timezone.utc)
    got2 = repo.get_as_of(IID_B, FactName.EPS, later)
    assert got2 is not None and got2.value == Decimal("1.3500000000")


def test_fact_get_missing_returns_none(engine):
    repo = SqlFundamentalFactRepository(engine)
    assert repo.get_as_of(IID_B, FactName.EPS,
                          datetime(2024, 1, 1, tzinfo=timezone.utc)) is None


def test_fact_history_respects_until(engine):
    repo = SqlFundamentalFactRepository(engine)
    repo.upsert_many([
        _fact(FactName.EPS, "1.0",
              as_of=datetime(2024, 1, 31, tzinfo=timezone.utc)),
        _fact(FactName.EPS, "1.1",
              as_of=datetime(2024, 4, 30, tzinfo=timezone.utc)),
        _fact(FactName.EPS, "1.2",
              as_of=datetime(2024, 7, 31, tzinfo=timezone.utc)),
    ])
    hist = repo.history(IID_B, FactName.EPS,
                        until=datetime(2024, 5, 1, tzinfo=timezone.utc))
    assert [f.value for f in hist] == [
        Decimal("1.0000000000"), Decimal("1.1000000000"),
    ]


# ==================================================================
# Fund NAV
# ==================================================================
def _nav(d: date, unit: str, *, source: str = "eastmoney",
         available: datetime | None = None) -> FundNAV:
    et = datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)
    return FundNAV(
        instrument_id=IID_F,
        market_local_date=d,
        event_time_utc=et,
        unit_nav=Decimal(unit),
        accum_nav=None,
        available_at_utc=available or et,
        source=source,
    )


def test_nav_upsert_and_range(engine):
    repo = SqlFundNavRepository(engine)
    repo.upsert_many([_nav(date(2024, 6, 10), "1.2345"),
                      _nav(date(2024, 6, 11), "1.2410")])
    out = repo.find_range(IID_F, date(2024, 6, 10), date(2024, 6, 11))
    assert len(out) == 2
    assert out[1].unit_nav == Decimal("1.241000")


def test_nav_pit_filter(engine):
    repo = SqlFundNavRepository(engine)
    late = datetime(2024, 6, 12, tzinfo=timezone.utc)
    repo.upsert_many([_nav(date(2024, 6, 10), "1.2345"),
                      _nav(date(2024, 6, 11), "1.2410",
                           available=late)])
    early = datetime(2024, 6, 11, 0, tzinfo=timezone.utc)
    out = repo.find_range(IID_F, date(2024, 6, 10), date(2024, 6, 11),
                          as_of_utc=early)
    assert [n.market_local_date for n in out] == [date(2024, 6, 10)]


# ==================================================================
# FX
# ==================================================================
def test_fx_upsert_and_lookup(engine):
    repo = SqlFxRepository(engine)
    for d, r in [("2024-06-10", "7.20"), ("2024-06-11", "7.18"),
                 ("2024-06-12", "7.22")]:
        repo.upsert(base="USD", quote="CNY",
                    d=date.fromisoformat(d), rate=Decimal(r),
                    available_at_utc=datetime.fromisoformat(f"{d}T12:00:00+00:00"),
                    source="ecb")
    rate = repo.get_as_of(base="USD", quote="CNY",
                          on_or_before=date(2024, 6, 11))
    assert rate == Decimal("7.1800000000")


def test_fx_pit_hides_future_rows(engine):
    repo = SqlFxRepository(engine)
    repo.upsert(base="USD", quote="CNY", d=date(2024, 6, 10),
                rate=Decimal("7.20"),
                available_at_utc=datetime(2024, 6, 10, 12, tzinfo=timezone.utc),
                source="ecb")
    repo.upsert(base="USD", quote="CNY", d=date(2024, 6, 11),
                rate=Decimal("7.18"),
                available_at_utc=datetime(2024, 6, 20, tzinfo=timezone.utc),
                source="ecb")
    early = datetime(2024, 6, 11, 6, tzinfo=timezone.utc)
    rate = repo.get_as_of(base="USD", quote="CNY",
                          on_or_before=date(2024, 6, 11), as_of_utc=early)
    assert rate == Decimal("7.2000000000")


# ==================================================================
# Portfolio positions
# ==================================================================
def test_position_upsert_and_snapshot(engine):
    repo = SqlPortfolioPositionRepository(engine)
    repo.upsert(portfolio_id="P1", instrument_id=IID_A,
                as_of_local_date=date(2024, 6, 10),
                quantity=Decimal("100"), currency="CNY",
                avg_cost_local=Decimal("1700"))
    repo.upsert(portfolio_id="P1", instrument_id=IID_A,
                as_of_local_date=date(2024, 6, 11),
                quantity=Decimal("150"), currency="CNY",
                avg_cost_local=Decimal("1710"))
    repo.upsert(portfolio_id="P1", instrument_id=IID_B,
                as_of_local_date=date(2024, 6, 11),
                quantity=Decimal("50"), currency="USD",
                avg_cost_local=Decimal("180"))
    snap = repo.snapshot("P1", as_of=date(2024, 6, 15))
    assert len(snap) == 2
    by_iid = {row["instrument_id"]: row for row in snap}
    assert by_iid["CN.SSE.EQUITY.600519"]["quantity"] == Decimal("150.00000000")
    assert by_iid["US.NASDAQ.EQUITY.AAPL"]["quantity"] == Decimal("50.00000000")
