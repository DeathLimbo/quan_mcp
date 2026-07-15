"""Point-in-time fundamentals store.

The store is intentionally minimal — a sorted list per (instrument, name).
The production wiring reads from Postgres, but the contract is identical:
callers never see rows with ``available_at_utc > as_of``.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable

from packages.common.errors import QuantError
from packages.common.instrument_id import InstrumentId


class FactName(str, Enum):
    """Canonical fundamental names. New entries require a data-quality contract."""
    REVENUE = "revenue"
    NET_INCOME = "net_income"
    EPS = "eps"
    BOOK_VALUE_PER_SHARE = "book_value_per_share"
    SHARES_OUTSTANDING = "shares_outstanding"
    DIVIDEND_PER_SHARE = "dividend_per_share"
    OPERATING_CASHFLOW = "operating_cashflow"
    TOTAL_DEBT = "total_debt"
    CASH_AND_EQUIV = "cash_and_equiv"
    # Fund-specific
    FUND_AUM = "fund_aum"
    FUND_EXPENSE_RATIO = "fund_expense_ratio"


class FundamentalsError(QuantError):
    pass


@dataclass(frozen=True, slots=True)
class Fact:
    instrument_id: InstrumentId
    name: FactName
    period_end: date              # e.g. 2025-12-31 for FY 2025
    value: Decimal
    currency: str | None          # None for ratios / unit-less
    as_of_utc: datetime           # filing / announcement time
    available_at_utc: datetime    # ingest cutoff (>= as_of_utc)
    source: str
    source_version: str

    def __post_init__(self) -> None:
        if self.available_at_utc < self.as_of_utc:
            raise FundamentalsError(
                "available_at_utc must be >= as_of_utc (PIT violation)"
            )


@dataclass(frozen=True, slots=True)
class PitQuery:
    instrument_id: InstrumentId
    name: FactName
    as_of: datetime


class FactStore:
    """In-memory PIT store.

    Facts are appended in arbitrary order; the store keeps them sorted by
    ``available_at_utc`` per (instrument, name) so that PIT reads are O(log n).
    """

    def __init__(self) -> None:
        # key = (canonical_id, name.value) -> list of (available_at_utc, Fact)
        self._buckets: dict[tuple[str, str], list[tuple[datetime, Fact]]] = {}

    def add(self, fact: Fact) -> None:
        key = (fact.instrument_id.canonical(), fact.name.value)
        bucket = self._buckets.setdefault(key, [])
        entry = (fact.available_at_utc, fact)
        # insort keyed on available_at_utc
        keys = [e[0] for e in bucket]
        pos = bisect.bisect_right(keys, fact.available_at_utc)
        bucket.insert(pos, entry)

    def add_many(self, facts: Iterable[Fact]) -> None:
        for f in facts:
            self.add(f)

    def get(self, query: PitQuery) -> Fact | None:
        key = (query.instrument_id.canonical(), query.name.value)
        bucket = self._buckets.get(key, [])
        if not bucket:
            return None
        keys = [e[0] for e in bucket]
        pos = bisect.bisect_right(keys, query.as_of) - 1
        if pos < 0:
            return None
        return bucket[pos][1]

    def history(self, instrument_id: InstrumentId, name: FactName,
                *, until: datetime | None = None) -> list[Fact]:
        key = (instrument_id.canonical(), name.value)
        bucket = self._buckets.get(key, [])
        if until is None:
            return [e[1] for e in bucket]
        return [e[1] for e in bucket if e[0] <= until]


def latest_as_of(facts: Iterable[Fact], as_of: datetime) -> Fact | None:
    """Convenience for callers that already hold a small list."""
    best: Fact | None = None
    for f in facts:
        if f.available_at_utc <= as_of and (best is None or f.available_at_utc > best.available_at_utc):
            best = f
    return best
