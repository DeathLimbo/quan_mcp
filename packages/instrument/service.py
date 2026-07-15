"""Instrument service.

Provides ``register``, ``get``, ``resolve_alias`` on top of the
``instruments`` and ``instrument_aliases`` tables (see 0001 migration).

Phase 1 exposes an in-memory store for unit-testing. A SQLAlchemy-backed
implementation follows the same interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from packages.calendar_rule import calendar_version, rule_version
from packages.common.errors import UnknownInstrumentError
from packages.common.instrument_id import InstrumentId
from packages.common.time_utils import utcnow


@dataclass(frozen=True, slots=True)
class InstrumentRow:
    instrument_id: InstrumentId
    name_local: str | None
    name_en: str | None
    currency: str
    lot_size: int | None
    first_trade_date: date | None
    last_trade_date: date | None
    status: str
    ingested_at_utc: datetime
    calendar_version: str
    rule_version: str


class InstrumentRepo(Protocol):
    def upsert(self, row: InstrumentRow) -> None: ...
    def get(self, iid: InstrumentId) -> InstrumentRow | None: ...
    def add_alias(self, alias: str, source: str, iid: InstrumentId, valid_from_utc: datetime) -> None: ...
    def resolve_alias(self, alias: str, source: str) -> InstrumentId | None: ...


@dataclass
class InMemoryInstrumentRepo:
    _rows: dict[str, InstrumentRow] = field(default_factory=dict)
    _aliases: dict[tuple[str, str], InstrumentId] = field(default_factory=dict)

    def upsert(self, row: InstrumentRow) -> None:
        self._rows[row.instrument_id.canonical()] = row

    def get(self, iid: InstrumentId) -> InstrumentRow | None:
        return self._rows.get(iid.canonical())

    def add_alias(self, alias: str, source: str, iid: InstrumentId, valid_from_utc: datetime) -> None:
        self._aliases[(alias, source)] = iid

    def resolve_alias(self, alias: str, source: str) -> InstrumentId | None:
        return self._aliases.get((alias, source))


class InstrumentService:
    def __init__(self, repo: InstrumentRepo) -> None:
        self._repo = repo

    def register(
        self,
        iid: InstrumentId,
        *,
        currency: str,
        name_local: str | None = None,
        name_en: str | None = None,
        lot_size: int | None = None,
        first_trade_date: date | None = None,
        last_trade_date: date | None = None,
        status: str = "ACTIVE",
    ) -> InstrumentRow:
        row = InstrumentRow(
            instrument_id=iid,
            name_local=name_local,
            name_en=name_en,
            currency=currency,
            lot_size=lot_size,
            first_trade_date=first_trade_date,
            last_trade_date=last_trade_date,
            status=status,
            ingested_at_utc=utcnow(),
            calendar_version=calendar_version(),
            rule_version=rule_version(),
        )
        self._repo.upsert(row)
        return row

    def get_required(self, iid: InstrumentId) -> InstrumentRow:
        row = self._repo.get(iid)
        if row is None:
            raise UnknownInstrumentError(f"instrument not registered: {iid.canonical()}")
        return row

    def add_alias(self, alias: str, source: str, iid: InstrumentId) -> None:
        # Ensure the instrument exists first
        self.get_required(iid)
        self._repo.add_alias(alias, source, iid, utcnow())

    def resolve(self, alias: str, source: str) -> InstrumentId:
        iid = self._repo.resolve_alias(alias, source)
        if iid is None:
            raise UnknownInstrumentError(f"alias not found: {source}:{alias}")
        return iid
